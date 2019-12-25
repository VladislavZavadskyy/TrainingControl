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
from .ui_wrappers import Field, Button, TextArea

import __main__ as main

__all__ = [
    'TrainingManager'
]


class TrainingManager(SyncManager):
    def __init__(
            self, log_metadir, tb_address, models, config, controls, tb_executable=None, save_every=1000
    ):
        super().__init__()

        assert config is not None

        self.script_file = main.__file__
        self.config = self._filter_out_maintenance_args(self.script_file, config)

        self.experiment_name = config['experiment_name'].lower().replace(' ', '_')

        self.tb_executable = tb_executable
        self.log_metadir = log_metadir

        if not (tb_address.startswith('http://') or tb_address.startswith('https://')):
            tb_address = 'http://' + tb_address

        match = re.match('(https?://[\w\d\.]+):(\d+)', tb_address)
        self.tb_ip, self.tb_port = match[1], int(match[2])

        self.controls = controls
        self._controls_by_name = {c.name: c for c in controls}
        assert len(self.controls) == len(self._controls_by_name)

        self.models = models
        self._write_experiment_to_index()

        self._processes_started = False
        self.termination_list = []

        self.steps_per_epoch = 0
        self.global_step = 0

        self.save_every = save_every

        self.print_config()

    @property
    def epoch(self):
        return self.global_step // self.steps_per_epoch

    @property
    def epoch_step(self):
        return self.global_step % self.steps_per_epoch

    @staticmethod
    def _filter_out_maintenance_args(script_file, config):
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

        return config

    def _write_experiment_to_index(self):

        os.makedirs(self.log_metadir, exist_ok=True)
        self.index_path = os.path.join(self.log_metadir, 'index.tsv')

        lock = FileLock(self.index_path + '.lock')
        with lock.acquire(timeout=10):
            if not os.path.exists(self.index_path):
                self.index = pandas.DataFrame(columns=['experiment_name'])
            else:
                self.index = pandas.read_csv(self.index_path, sep='\t')

            config_ = {k: f'"{v}"' if isinstance(v, str) else v for k, v in self.config.items()}
            query_string = '&'.join(f'{k}=={v if isinstance(v, str) else v}' for k, v in config_.items())

            with suppress(UndefinedVariableError):
                query_result = self.index.query(query_string)

                if len(query_result) > 0:
                    print("Experiment (or few) with the same configuration already exists: ")
                    print('\t' + ', '.join(query_result['experiment_name'].tolist()))
                    if input('Do you wish to continue? [y/N]: ').lower() != 'y':
                        sys.exit(0)

            self._prepare_directory()
            self.index = self.index.append(
                pandas.DataFrame([
                    dict(
                        **self.config,
                        time_ran=datetime.now().strftime('%d %b %Y, %H:%M'),
                    ),
                ]), sort=False, ignore_index=True)

            self.index.to_csv(self.index_path, '\t', index=False)

    def _prepare_directory(self):
        self.log_dir = os.path.join(self.log_metadir, self.experiment_name)

        if os.path.exists(self.log_dir):
            answ = input(
                f'Logging directory for experiment name "{self.experiment_name}" already exists. '
                'Overwrite TB events? [y/N]: '
            )
            if answ.lower() == 'y':
                if input(
                    f'Would you like to make a backup? [Y/n]: '
                ) != 'n':
                    print('Backing up...')
                    shutil.make_archive(
                        os.path.join(self.log_metadir, f'{self.experiment_name}_backup'), 'zip', self.log_dir
                    )

                for f in os.listdir(self.log_dir):
                    if 'events.out.tfevents' in f:
                        os.remove(os.path.join(self.log_dir, f))

            self.index = self.index[self.index['experiment_name'] != self.config['experiment_name']]

        os.makedirs(self.log_dir, exist_ok=True)
        print(f'Writing logs to {self.log_dir}')

        with open(os.path.join(self.log_dir, 'experiment_config.json'), 'w') as f:
            json.dump(self.config, f)

        shutil.copy(self.script_file, os.path.join(self.log_dir, 'training_script.py'))

    def print_config(self):
        print("Current configuration: ")
        for k, v in self.config.items():
            print(f'\t{k}: {v}')

    def set_callback(self, control_name, callback):
        self._controls_by_name[control_name].callback = callback

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

        p = Process(target=_target, args=(
            self.tb_port + 1, self.tb_ip, self.config, self.controls, self.request_queue, self.response_queue
        ))
        p.start()

        self.termination_list.append(p)
        print(f'Started control server at {self.tb_ip}:{self.tb_port+1}')
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
                print(f'Couldn\'t find state for {k} in {os.path.dirname(checkpoint_path)}')
                if input('Continue? [Y/n]').lower() == 'n': sys.exit(0)

        meta_path = os.path.join(self.log_dir, checkpoint_name, 'meta.json')
        if not os.path.exists(meta_path):
            abspath = os.path.join(checkpoint_name, f'meta.json')
            if os.path.exists(abspath):
                meta_path = abspath

        if os.path.exists(meta_path):
            with open(os.path.join(self.log_dir, checkpoint_name, 'meta.json'), 'r') as f:
                meta = json.load(f)

            self.global_step = meta['global_step']

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

        with open(os.path.join(self.log_dir, name, 'meta.json'), 'w') as f:
            json.dump({
                'global_step': self.global_step,
            }, f)

        return os.path.join(self.log_dir, name)

    def update(self, blocking=False):

        if self.global_step % self.save_every == (self.save_every - 1):
            self.save_models()
        self.global_step += 1

        if blocking:
            request = self.request_queue.get()
        else:
            try:
                request = self.request_queue.get_nowait()
            except queue.Empty:
                return

        try:
            key = next(iter(request.keys()))
            ui_element = self._controls_by_name[key]
            if isinstance(ui_element, Field):
                response = ui_element.callback(request[key][0])
            elif isinstance(ui_element, Button):
                response = ui_element.callback()
            elif isinstance(ui_element, TextArea):
                response = ui_element.callback(request[key][0])
            else:
                raise ValueError(f"Unknown UI element: {ui_element}")

            self.response_queue.put(str(response))

        except Exception as e:
            self.response_queue.put(str(e))

    def __enter__(self):
        super().__enter__()
        self.start_servers()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_models()

        if self.termination_list is not None:
            for p in self.termination_list:
                p.terminate() if hasattr(p, 'terminate') else p.kill()
