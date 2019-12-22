from setuptools import setup, find_packages

INSTALL_REQUIREMENTS = ['torch', 'pandas', 'tornado']

setup(
    name='training_control',
    version='0.1.0',

    author='Major Tom',
    license='WTFPL',

    packages=find_packages(),
    install_requires=INSTALL_REQUIREMENTS,
)
