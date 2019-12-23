from setuptools import setup, find_packages
import os

INSTALL_REQUIREMENTS = ['torch', 'pandas', 'tornado']

setup(
    name='training_control',
    version='0.1.0',

    author='Major Tom',
    license='WTFPL',

    packages=find_packages(),
    install_requires=INSTALL_REQUIREMENTS,
    package_data={
        'training_control': [
            os.path.join('static', 'main.js'),
            os.path.join('static', 'main.css'),
            os.path.join('templates', 'index.html'),
        ]
    },
)
