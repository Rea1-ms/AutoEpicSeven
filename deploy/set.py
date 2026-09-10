import sys
import typing as t

from deploy.Windows.config import DeployConfig

"""
Set config/deploy.yaml with commands like

python -m deploy.set GitExecutable=/usr/bin/git PythonExecutable=/usr/bin/python3.8
"""


def get_args() -> t.Dict[str, str]:
    args = {}
    for arg in sys.argv[1:]:
        if '=' not in arg:
            continue
        k, v = arg.split('=')
        k, v = k.strip(), v.strip()
        args[k] = v
    return args


def config_set(modify: t.Dict[str, str], output='./config/deploy.yaml') -> t.Dict[str, str]:
    """
    Args:
        modify: A dict of key-value in deploy.yaml
        output:

    Returns:
        The updated key-value in deploy.yaml
    """
    config = DeployConfig(file=output)
    for k, v in modify.items():
        if k in config.config:
            default = getattr(config, k)
            if isinstance(default, bool):
                value = v.lower() == 'true'
            elif isinstance(default, int):
                value = int(v)
            elif v.lower() == 'null':
                value = None
            else:
                value = v
            if not config.set(k, value):
                print(f'Value for key "{k}" is invalid')
                continue
            print(f'Key "{k}" set')
        else:
            print(f'Key "{k}" not exist')
    config.write()
    return config.config


if __name__ == '__main__':
    config_set(get_args())
