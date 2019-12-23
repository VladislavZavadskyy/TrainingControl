## Training Control
###### To major Tom

Example usage
```python
from training_control import TrainingManager, Field, Button, TextArea
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--some_important_arg', type=int)
    
    # comments to indicate unimportant arguments (those won't be written to index)
    # region maintenance args
    parser.add_argument('--save_every', type=int, default=1000, help='Interval of model saving')
    parser.add_argument('--device', type=str, default='cuda', help='device to train on')
    parser.add_argument('--port', type=int, default=8888)
    ...
    # endregion

    def lr_callback(value):
        for pg in optimizer.param_groups:
            pg['lr'] = float(value)
        return f'Set learning rate to {value}'
        
    with TrainingManager(
            expanduser('~/training/'),
            f'0.0.0.0:{args.port}',
            model_dict, args.__dict__,
            [
                Field('lr', f'Learning rate', lr_callback),
                # Code is evaluated in global context
                TextArea('eval', 'Evaluate', lambda x: str(eval(x))),
                ...
            ]
    ) as manager:

        manager.load_models(args.load)
        
        # Set callback later
        manager.set_callback('save', lambda p: f'Saved models to {manager.save_models(p.decode("utf-8"))}')

        while True:
        
            # update every step
            manager.update(blocking=False)

```