#!/usr/bin/env python

import setuptools

setuptools.setup(
    name='dmcast',
    packages=['dmcast'],
    version='0.0.3',
    license='EPL',
    description='simple chromecast utility',
    url='https://github.com/xlfe/dmcast',
    keywords=[
        'python', 'chromecast', 'internet-of-things',
        'chromecast-audio', 'cast'],
    install_requires=[
        'protobuf>=2.6.0'
    ],
    classifiers=[
     'Development Status :: 3 - Alpha',
     'Intended Audience :: Developers',
     'License :: OSI Approved :: Eclipse Public License 2.0 (EPL-2.0)',
     'Programming Language :: Python :: 3.9',
    ]
)

