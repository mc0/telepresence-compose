#!/usr/bin/env python3

'''
Parse docker-compose.yml and start telepresence for the specified service
'''

import itertools
import subprocess
import sys
import os
from argparse import ArgumentParser
from pathlib import PosixPath

from datetime import timedelta
import re

import yaml

'''
Does not support the following yet:
cgroup_parent, credential_spec, depends_on, deploy, external_links, healthcheck, init,
isolation, logging, network_mode, networks, secrets, security_opt, sysctls, tmpfs,
ulimits, userns_mode

domainname, hostname, ipc, mac_address, privileged, read_only, shm_size, stdin_open, tty, user

or the features:
- top-level networks (aka "external" networks)
- top-level volumes (aka "volumes-from")
- extensions
- variable substitution

or these volume features:
- bind-propagation
- volume-nocopy
- tmpfs-size
- tmpfs-mode

These will not be supported:
- links: deprecated functionality
- restart: conflicts with --rm
'''

def port_to_str(port):
    if isinstance(port, str):
        return port
    if isinstance(port, dict):
        return str(port['published']) + ':' + str(port['target']) + '/' + port['protocol']
    return null

'''
type: the mount type volume, bind, tmpfs or npipe
source: the source of the mount, a path on the host for a bind mount, or the name of a volume defined in the top-level volumes key. Not applicable for a tmpfs mount.
target: the path in the container where the volume is mounted
read_only: flag to set the volume as read-only
bind: configure additional bind options
    propagation: the propagation mode used for the bind
volume: configure additional volume options
    nocopy: flag to disable copying of data from a container when a volume is created
tmpfs: configure additional tmpfs options
    size: the size for the tmpfs mount in bytes
consistency: the consistency requirements of the mount, one of consistent (host and container have identical view), cached (read cache, host view is authoritative) or delegated (read-write cache, container’s view is authoritative)
'''
def volume_dict_pairs_to_str(args, keyval_arr):
    key = keyval_arr[0]
    val = keyval_arr[1]
    if key == 'read_only':
        return 'readonly=' + str(val)
    if key == 'source':
        if val.startswith('~'):
            path = PosixPath(str(val)).expanduser()
            return 'source=' + str(os.path.normpath(path.absolute()))
        if val.startswith('/') == False:
            dirname = os.path.dirname(args.composefile)
            path = PosixPath(dirname, str(val))
            return 'source=' + str(os.path.normpath(path.absolute()))
        return 'source=' + str(val)
    return key + '=' + str(val)

def get_volume_dict_pairs_to_str(args):
    return lambda x: volume_dict_pairs_to_str(args, x)

def volume_to_str_lambda(args, volume):
    if isinstance(volume, str):
        return '-v ' + volume
    if isinstance(volume, dict):
        return '--mount ' + ','.join(list(map(get_volume_dict_pairs_to_str(args), volume.items())))
    return null

def env_file_to_str_lambda(args, env_file):
    if env_file.startswith('~'):
        path = PosixPath(str(env_file)).expanduser()
        return '--env-file ' + str(os.path.normpath(path.absolute()))
    if env_file.startswith('/') == False:
        dirname = os.path.dirname(args.composefile)
        path = PosixPath(dirname, str(env_file))
        return '--env-file ' + str(os.path.normpath(path.absolute()))
    return '--env-file ' + env_file

def get_volume_to_str_lambda(args):
    return lambda x: volume_to_str_lambda(args, x)

def get_env_file_to_str_lambda(args):
    return lambda x: env_file_to_str_lambda(args, x)

'''
Convert time to seconds for timeout
'''
UNITS = {'s':'seconds', 'm':'minutes', 'h':'hours', 'd':'days', 'w':'weeks'}

def convert_to_seconds(s):
    return int(timedelta(**{
        UNITS.get(m.group('unit').lower(), 'seconds'): int(m.group('val'))
        for m in re.finditer(r'(?P<val>\d+)(?P<unit>[smhdw]?)', s, flags=re.I)
    }).total_seconds())

def main(args):
    with open(args.composefile) as file:
        compose_file = yaml.load(file, Loader=yaml.FullLoader)
    if not args.context:
        sys.exit('context MUST be passed')
    if not args.service in compose_file['services']:
        sys.exit('Service {} not found in {}'.format(args.service, compose_file['services'].keys()))

    svc_entrypoint_raw = compose_file['services'][args.service].get('entrypoint')
    svc_entrypoint = ' '.join(svc_entrypoint_raw) if isinstance(svc_entrypoint_raw, list) else svc_entrypoint_raw
    svc_entrypoint_split = svc_entrypoint.split(' ') if svc_entrypoint else []
    svc_command = compose_file['services'][args.service].get('command')
    svc_image = compose_file['services'][args.service].get('image')
    svc_name = compose_file['services'][args.service].get('container_name')
    svc_pid = compose_file['services'][args.service].get('pid')
    svc_stop_grace_period = compose_file['services'][args.service].get('stop_grace_period')
    svc_stop_signal = compose_file['services'][args.service].get('stop_signal')
    svc_working_dir = compose_file['services'][args.service].get('working_dir')

    svc_cap_add = compose_file['services'][args.service].get('cap_add') or []
    svc_cap_drop = compose_file['services'][args.service].get('cap_drop') or []
    svc_devices = compose_file['services'][args.service].get('devices') or []
    svc_dns = compose_file['services'][args.service].get('dns') or []
    svc_dns_search = compose_file['services'][args.service].get('dns_search') or []
    svc_env_file = compose_file['services'][args.service].get('env_file') or []
    svc_environment = compose_file['services'][args.service].get('environment') or []
    svc_expose = compose_file['services'][args.service].get('expose') or []
    svc_extra_hosts = compose_file['services'][args.service].get('extra_hosts') or []
    svc_labels = compose_file['services'][args.service].get('labels') or []

    svc_ports_raw = compose_file['services'][args.service].get('ports') or []
    svc_ports = list(map(port_to_str, svc_ports_raw))

    # TODO: convert these all to simple strings, some may be objects; see ports
    svc_volumes_raw = compose_file['services'][args.service].get('volumes') or []

    cap_add_list = itertools.chain.from_iterable(zip(itertools.repeat('--cap-add', len(svc_cap_add)), svc_cap_add))
    cap_drop_list = itertools.chain.from_iterable(zip(itertools.repeat('--cap-drop', len(svc_cap_drop)), svc_cap_drop))
    devices_list = itertools.chain.from_iterable(zip(itertools.repeat('--devices', len(svc_devices)), svc_devices))
    dns_list = itertools.chain.from_iterable(zip(itertools.repeat('--dns', len(svc_dns)), svc_dns))
    dns_search_list = itertools.chain.from_iterable(zip(itertools.repeat('--dns-search', len(svc_dns_search)), svc_dns_search))
    environment_list = itertools.chain.from_iterable(zip(itertools.repeat('-e', len(svc_environment)), svc_environment))
    expose_list = itertools.chain.from_iterable(zip(itertools.repeat('--expose', len(svc_expose)), svc_expose))
    extra_host_list = itertools.chain.from_iterable(zip(itertools.repeat('--add-host', len(svc_extra_hosts)), svc_extra_hosts))
    label_list = itertools.chain.from_iterable(zip(itertools.repeat('-l', len(svc_labels)), svc_labels))
    port_list = itertools.chain.from_iterable(zip(itertools.repeat('-p', len(svc_ports)), svc_ports))

    # env_file_list = itertools.chain.from_iterable(zip(itertools.repeat('--env-file', len(svc_env_file)), svc_env_file))
    env_file_list = list(map(get_env_file_to_str_lambda(args), svc_env_file))
    volume_list = list(map(get_volume_to_str_lambda(args), svc_volumes_raw))

    # build a command by exending/appending to an array
    cmd = ['telepresence']
    cmd.extend(['--method', 'container'])
    cmd.extend(['--context', args.context])

    if args.swap:
        cmd.extend(['--swap-deployment', args.service])
    else:
        cmd.extend(['--new-deployment', 'tele-' + args.service])

    # TODO: this needs to also incorporate any ports specified
    cmd.extend(expose_list)
    cmd.extend(['--mount', 'false'])
    cmd.extend(['--env-file', 'telepresence-env-file.env.tmp'])
    # now all the rest is docker config
    cmd.extend(['--docker-run', '--rm', '-it'])
    if svc_name:
        cmd.extend(['--name', svc_name])
    if svc_entrypoint_split:
        cmd.extend(['--entrypoint', svc_entrypoint_split[0]])
    if svc_pid:
        cmd.extend(['--pid', svc_pid])
    if svc_stop_grace_period:
        cmd.extend(['--stop-timeout', str(convert_to_seconds(svc_stop_grace_period))])
    if svc_stop_signal:
        cmd.extend(['--stop-signal', svc_stop_signal])
    if svc_working_dir:
        cmd.extend(['--workdir', svc_working_dir])

    cmd.extend(cap_add_list)
    cmd.extend(cap_drop_list)
    cmd.extend(devices_list)
    cmd.extend(dns_list)
    cmd.extend(dns_search_list)
    cmd.extend(env_file_list)
    cmd.extend(environment_list)
    cmd.extend(expose_list)
    cmd.extend(extra_host_list)
    cmd.extend(label_list)
    cmd.extend(port_list)
    cmd.extend(volume_list)
    cmd.append(svc_image)

    if len(svc_entrypoint_split) > 1:
        cmd.extend(svc_entrypoint_split[1:])

    # subprocess.run(cmd)
    finalCmd = ' '.join(cmd)
    print('Docker command:\n' + finalCmd)

if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('-s', '--service', help='Service name to transform', required=True)
    parser.add_argument('-c', '--context', help='Kube context', required=True)
    parser.add_argument('-S', '--swap', help='Should we replace the deployment?', default=False, action='store_true')
    parser.add_argument('composefile', help='Path to docker-compose.yaml')
    args = parser.parse_args()
    main(args)
