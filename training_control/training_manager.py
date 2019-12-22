import os
import socket
import subprocess

from multiprocessing import Process
from multiprocessing.managers import SyncManager

import torch
import shutil
import pandas
import queue

from filelock import FileLock
from datetime import datetime

import re
import json
import sys

from pandas.core.computation.ops import UndefinedVariableError
from contextlib import suppress

from .server import _target
from .ui_wrappers import Field, Button

__all__ = [
    'TrainingManager'
]


class TrainingManager(SyncManager):
    def __init__(
            self, log_metadir, models, tb_port, config, script_file, ui, tb_executable=None, tb_ip=None
    ):
        super().__init__()

        assert config is not None

        with open(script_file) as f:
            code = f.read()

        opening = re.search('\# region maintenance args', code)
        if opening:
            opening = opening.span()[1]
            closing = [i.span()[0] for i in list(re.finditer('\# endregion', code))]
            if len(closing) > 0:
                closing = min([c for c in closing if c >= opening])

            maintenance_args = re.findall('parser.add_argument\([\'"]--([\w\d_\-]+)', code[opening:closing])
            maintenance_args = [a.replace('-', '_') for a in maintenance_args]

            config = {k: v for k,v in config.items() if k not in maintenance_args}

        self.experiment_name = config['experiment_name'].lower().replace(' ', '_')

        self.tb_executable = tb_executable
        self.log_metadir = log_metadir
        self.script_file = script_file

        self.tb_port = tb_port
        self.tb_ip = tb_ip

        self.ui = ui
        self._ui_by_name = {u.name: u for u in ui}

        self.models = models
        self.config = config

        self._print_config()

        os.makedirs(self.log_metadir, exist_ok=True)
        self.index_path = os.path.join(log_metadir, 'index.tsv')

        lock = FileLock(self.index_path + '.lock')
        with lock.acquire(timeout=10):
            if not os.path.exists(self.index_path):
                self.index = pandas.DataFrame(columns=['experiment_name'])
            else:
                self.index = pandas.read_csv(self.index_path, sep='\t')

            config_ = {k: f'"{v}"' if isinstance(v, str) else v for k, v in config.items()}
            query_string = '&'.join(f'{k}=={v if isinstance(v, str) else v}' for k, v in config_.items())

            try:
                query_result = self.index.query(query_string)

                if len(query_result) > 0:
                    print("Experiment (or few) with the same configuration already exists: ")
                    print('\t' + ', '.join(query_result['experiment_name'].tolist()))
                    if input('Do you wish to continue? [y/N]: ').lower() != 'y':
                        sys.exit(0)

            except UndefinedVariableError:
                pass

            self._prepare_directory()
            self.index = self.index.append(
                pandas.DataFrame([
                    dict(
                        **self.config,
                        time_ran=datetime.now().strftime('%d %b %Y, %H:%M'),
                    ),
                ]), sort=False, ignore_index=True)

            self.index.to_csv(self.index_path, '\t', index=False)

        self._processes_started = False
        self.termination_list = []

    def _print_config(self):
        print("Current configuration: ")
        for k, v in self.config.items():
            print(f'\t{k}: {v}')

    def _prepare_directory(self):
        self.log_dir = os.path.join(self.log_metadir, self.experiment_name)

        while os.path.exists(self.log_dir):
            answ = input(
                f'Logging directory for experiment name "{self.experiment_name}"" already exists. Overwrite? [y/N]: '
            )
            if answ.lower() == 'y':
                shutil.rmtree(self.log_dir)
                self.index = self.index[self.index['experiment_name'] != self.config['experiment_name']]
            else:
                sys.exit(0)

        print(f'Writing logs to {self.log_dir}')

        os.makedirs(self.log_dir, exist_ok=True)
        for f in os.listdir(self.log_dir):
            if 'events.out.tfevents' in f:
                os.remove(os.path.join(self.log_dir, f))

        with open(os.path.join(self.log_dir, 'experiment_config.json'), 'w') as f:
            json.dump(self.config, f)

        shutil.copy(self.script_file, os.path.join(self.log_dir, 'training_script.py'))

    def start_servers(self):
        if self._processes_started:
            print('Processes are already started')
            return

        if self.tb_executable is None:
            self.tb_executable = subprocess.getoutput(['which tensorboard'])

        tb_proc = subprocess.Popen(
            [self.tb_executable, '--port', str(self.tb_port), '--logdir', self.log_dir, '--host', '0.0.0.0']
        )
        self.termination_list.append(tb_proc)

        self.request_queue = self.Queue()
        self.response_queue = self.Queue()

        p = Process(target=_target, args=(self.tb_port+1, self.tb_ip, self.config, self.ui, self.request_queue, self.response_queue))
        p.start()

        self.termination_list.append(p)
        print(f'Started control server at {self.tb_port+1}')
        self._processes_started = True

    def load_models(self, checkpoint_name):
        if not checkpoint_name: return
        for k, v in self.models.items():
            checkpoint_path = os.path.join(self.log_dir, checkpoint_name, f'{k}.pth')

            if not os.path.exists(checkpoint_path):
                abspath = os.path.join(checkpoint_name, f'{k}.pth')
                if os.path.exists(abspath):
                    checkpoint_path = abspath

            if os.path.exists(checkpoint_path):
                if isinstance(v, torch.nn.DataParallel):
                    v = v.module

                state = torch.load(checkpoint_path)

                if isinstance(v, torch.optim.Optimizer):
                    for state_key in state:
                        v.state[state_key] = state[state_key]
                elif isinstance(v, torch.nn.Parameter):
                    v.data = state.data
                else:
                    v.load_state_dict(state)

                print(f'Loaded {k} state from {checkpoint_path} successfully.')
            else:
                print(f'Couldn\'t find state for {k} in {os.path.dirname(checkpoint_path)}.')
                if input('Continue? [Y/n]').lower() == 'n': sys.exit(0)

    def save_models(self, name='latest'):
        os.makedirs(os.path.join(self.log_dir, name), exist_ok=True)
        for k, v in self.models.items():
            if isinstance(v, torch.nn.DataParallel):
                v = v.module
            if isinstance(v, torch.optim.Optimizer):
                torch.save(v.state, os.path.join(self.log_dir, name, f'{k}.pth'))
            elif isinstance(v, torch.nn.Parameter):
                torch.save(v, os.path.join(self.log_dir, name, f'{k}.pth'))
            else:
                torch.save(v.state_dict(), os.path.join(self.log_dir, name, f'{k}.pth'))
        return os.path.join(self.log_dir, name)

    def update(self, blocking=False):
        if blocking:
            request = self.request_queue.get()
        else:
            with suppress(queue.Empty):
                request = self.request_queue.get_nowait()

        try:
            key = next(iter(request.keys()))
            ui_element = self._ui_by_name[key]
            if isinstance(ui_element, Field):
                response = ui_element.callback(request[key])
            elif isinstance(ui_element, Button):
                response = ui_element.callback()
            else:
                raise ValueError(f"Unknown name: {key}")

            tm.response_queue.put(str(response))

        except Exception as e:
            tm.response_queue.put(str(e))

    def __enter__(self):
        super().__enter__()
        self.start_servers()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_models()

        if self.termination_list is not None:
            for p in self.termination_list:
                p.terminate() if hasattr(p, 'terminate') else p.kill()
