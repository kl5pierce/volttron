# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:

# Copyright (c) 2013, Battelle Memorial Institute
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met: 
# 
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer. 
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution. 
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# 
# The views and conclusions contained in the software and documentation
# are those of the authors and should not be interpreted as representing
# official policies, either expressed or implied, of the FreeBSD
# Project.
#
# This material was prepared as an account of work sponsored by an
# agency of the United States Government.  Neither the United States
# Government nor the United States Department of Energy, nor Battelle,
# nor any of their employees, nor any jurisdiction or organization that
# has cooperated in the development of these materials, makes any
# warranty, express or implied, or assumes any legal liability or
# responsibility for the accuracy, completeness, or usefulness or any
# information, apparatus, product, software, or process disclosed, or
# represents that its use would not infringe privately owned rights.
# 
# Reference herein to any specific commercial product, process, or
# service by trade name, trademark, manufacturer, or otherwise does not
# necessarily constitute or imply its endorsement, recommendation, or
# favoring by the United States Government or any agency thereof, or
# Battelle Memorial Institute. The views and opinions of authors
# expressed herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
# 
# PACIFIC NORTHWEST NATIONAL LABORATORY
# operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830

# pylint: disable=W0142,W0403
#}}}

import argparse
from contextlib import closing
import logging
from logging import handlers
import os
import sys

import gevent
from pkg_resources import load_entry_point
from zmq import green as zmq

from . import __version__
from . import config
from .control.server import control_loop
from .agent import utils

try:
    from volttron.restricted import aip
except ImportError:
    from . import aip
try:
    from volttron.restricted import resmon
except ImportError:
    resmon = None


_log = logging.getLogger(os.path.basename(sys.argv[0])
                         if __name__ == '__main__' else __name__)
_log.setLevel(logging.DEBUG)


def log_to_file(file, level=logging.WARNING,
                handler_class=logging.StreamHandler):
    '''Direct log output to a file (or something like one).'''
    handler = handler_class(file)
    handler.setLevel(level)
    handler.setFormatter(utils.AgentFormatter(
            '%(asctime)s %(composite_name)s %(levelname)s: %(message)s'))
    root = logging.getLogger()
    root.addHandler(handler)


def agent_exchange(in_addr, out_addr, logger_name=None):
    '''Agent message publish/subscribe exchange loop

    Accept multi-part messages from sockets connected to in_addr, which
    is a PULL socket, and forward them to sockets connected to out_addr,
    which is a XPUB socket. When subscriptions are added or removed, a
    message of the form 'subscriptions/<OP>/<TOPIC>' is broadcast to the
    PUB socket where <OP> is either 'add' or 'remove' and <TOPIC> is the
    topic being subscribed or unsubscribed. When a message is received
    of the form 'subscriptions/list/<PREFIX>', a multipart message will
    be broadcast with the first two received frames (topic and headers)
    sent unchanged and with the remainder of the message containing
    currently subscribed topics which start with <PREFIX>, each frame
    containing exactly one topic.

    If logger_name is given, a new logger will be created with the given
    name. Otherwise, the module logger will be used.
    '''
    log = _log if logger_name is None else logging.getLogger(logger_name)
    ctx = zmq.Context.instance()
    with closing(ctx.socket(zmq.PULL)) as in_sock, \
            closing(ctx.socket(zmq.XPUB)) as out_sock:
        in_sock.bind(in_addr)
        out_sock.bind(out_addr)
        poller = zmq.Poller()
        poller.register(in_sock, zmq.POLLIN)
        poller.register(out_sock, zmq.POLLIN)
        subscriptions = set()
        while True:
            for sock, event in poller.poll():
                if sock is in_sock:
                    message = in_sock.recv_multipart()
                    log.debug('incoming message: {!r}'.format(message))
                    topic = message[0]
                    if (topic.startswith('subscriptions/list') and
                            topic[18:19] in ['/', '']):
                        if len(message) > 2:
                            del message[2:]
                        elif len(message) == 1:
                            message.append('')
                        prefix = topic[19:]
                        message.extend([t for t in subscriptions
                                        if t.startswith(prefix)])
                    out_sock.send_multipart(message)
                elif sock is out_sock:
                    message = out_sock.recv()
                    if message:
                        add = bool(ord(message[0]))
                        topic = message[1:]
                        if add:
                            subscriptions.add(topic)
                        else:
                            subscriptions.discard(topic)
                        log.debug('incoming subscription: {} {!r}'.format(
                                ('add' if add else 'remove'), topic))
                        out_sock.send('subscriptions/{}{}{}'.format(
                                ('add' if add else 'remove'),
                                ('' if topic[:1] == '/' else '/'), topic))


def main(argv=sys.argv):
    # Setup option parser
    progname = os.path.basename(argv[0])
    parser = config.ArgumentParser(usage='%(prog)s [OPTION]...',
        prog=progname, add_help=False,
        description='VOLTTRON platform service',
        parents=[config.get_volttron_parser()],
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument('--show-config', action='store_true',
        help=argparse.SUPPRESS)
    parser.add_help_argument()
    parser.add_version_argument(version='%(prog)s ' + __version__)

    agents = parser.add_argument_group('agent options')
    agents.add_argument('--autostart', action='store_true', inverse='--no-autostart',
        help='automatically start enabled agents and services')
    agents.add_argument('--no-autostart', action='store_false', dest='autostart',
        help=argparse.SUPPRESS)
    agents.add_argument('--publish-address', metavar='ZMQADDR',
        help='ZeroMQ URL for used for agent publishing')
    agents.add_argument('--subscribe-address', metavar='ZMQADDR',
        help='ZeroMQ URL for used for agent subscriptions')

    control = parser.add_argument_group('control options')
    control.add_argument('--control-socket', metavar='FILE',
        help='path to socket used for control messages')
    control.add_argument('--allow-root', action='store_true', inverse='--no-allow-root',
        help='allow root to connect to control socket')
    control.add_argument('--no-allow-root', action='store_false', dest='allow_root',
        help=argparse.SUPPRESS)
    control.add_argument('--allow-users', action='store_list',
        help='users allowed to connect to control socket')
    control.add_argument('--allow-groups', action='store_list',
        help='user groups allowed to connect to control socket')

    if resmon is not None:
        restrict = parser.add_argument_group('restricted options')
        restrict.add_argument('--resource-monitor', action='store_true',
            inverse='--no-resource-monitor',
            help='enable agent resource management')
        restrict.add_argument('--no-resource-monitor', action='store_false',
            dest='resource_monitor', help=argparse.SUPPRESS)

    parser.set_defaults(**config.get_volttron_defaults())

    # Parse and expand options
    opts = parser.parse_args(argv[1:])
    expandall = lambda string: os.path.expandvars(os.path.expanduser(string))
    opts.volttron_home = expandall(os.environ.get('VOLTTRON_HOME', '~/.volttron'))
    os.environ['VOLTTRON_HOME'] = opts.volttron_home
    opts.control_socket = expandall(opts.control_socket)
    opts.publish_address = expandall(opts.publish_address)
    opts.subscribe_address = expandall(opts.subscribe_address)
    if getattr(opts, 'show_config', False):
        for name, value in sorted(vars(opts).iteritems()):
            print name, repr(value)
        return

    # Configure logging
    level = max(1, opts.verboseness)
    if opts.log is None:
        log_to_file(sys.stderr, level)
    elif opts.log == '-':
        log_to_file(sys.stdout, level)
    elif opts.log:
        log_to_file(opts.log, level, handler_class=handlers.WatchedFileHandler)
    else:
        log_to_file(None, 100, handler_class=lambda x: logging.NullHandler())
    if opts.log_config:
        logging.config.fileConfig(opts.log_config)

    # Set configuration
    if resmon is not None and opts.resource_monitor:
        _log.info('Resource monitor enabled')
        opts.resmon = resmon.ResourceMonitor()
    else:
        opts.resmon = None
    opts.aip = aip.AIPplatform(opts)
    opts.aip.setup()
    if opts.autostart:
        for name, error in opts.aip.autostart():
            _log.error('error starting {!r}: {}\n'.format(name, error))


    # Main loops
    try:
        exchange = gevent.spawn(
            agent_exchange, opts.publish_address, opts.subscribe_address)
        try:
            control = gevent.spawn(control_loop, opts)
            exchange.link(lambda *a: control.kill())
            control.join()
        finally:
            exchange.kill()
    finally:
        opts.aip.finish()


def _main():
    '''Entry point for scripts.'''
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    _main()
