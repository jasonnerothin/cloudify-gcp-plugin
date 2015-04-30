########
# Copyright (c) 2014 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.
import json
import httplib2
import time

from googleapiclient.discovery import build
from oauth2client.client import SignedJwtAssertionCredentials
import yaml


CONFIG = 'inputs_gcp.yaml'


def list_instances(compute, config):
    result = compute.instances().list(project=config['project'],
                                      zone=config['zone']).execute()
    return result['items']


def create_instance(compute, config_input):
    source_disk_image = config_input['manager_image']
    machine_type = 'zones/%s/machineTypes/n1-standard-1' % config_input['zone']
    startup_script = open('startup-script.sh', 'r').read()

    config = {
        'name': config_input['name'],
        'machineType': machine_type,

        'disks': [
            {
                'boot': True,
                'autoDelete': True,
                'initializeParams': {
                    'sourceImage': source_disk_image,
                }
            }
        ],
        'networkInterfaces': [{
            'network': 'global/networks/{0}'.format(config_input['network']),
            'accessConfigs': [
                {'type': 'ONE_TO_ONE_NAT', 'name': 'External NAT'}
            ]
        }],
        'serviceAccounts': [{
            'email': 'default',
            'scopes': [
                'https://www.googleapis.com/auth/devstorage.read_write',
                'https://www.googleapis.com/auth/logging.write'
            ]
        }],
        'metadata': {
            'items': [{
                'key': 'startup-script',
                'value': startup_script
            }, {
                'key': 'bucket',
                'value': config_input['project']
            }]
        }
    }

    return compute.instances().insert(
        project=config_input['project'],
        zone=config_input['zone'],
        body=config).execute()


def wait_for_operation(compute, config, operation, global_operation=False):
    print 'Waiting for operation to finish'
    while True:
        if global_operation:
            result = compute.globalOperations().get(
                project=config['project'],
                operation=operation).execute()
        else:
            result = compute.zoneOperations().get(
                project=config['project'],
                zone=config['zone'],
                operation=operation).execute()

        if result['status'] == 'DONE':
            print 'Done'
            if 'error' in result:
                raise Exception(result['error'])
            return result
        else:
            time.sleep(1)


def allow_http(compute, config):
    print 'Add firewall rule'
    body = {
        'sourceRanges': [config['http_cidr_enabled']],
        'name': '{0}-allow-http'.format(config['network']),
        'allowed': [
            {
                'IPProtocol': 'tcp',
                'ports': ['80']
            }
        ],
        'network': 'global/networks/{0}'.format(config['network'])
    }
    operation = compute.firewalls().insert(project=config['project'],
                                           body=body).execute()
    wait_for_operation(compute, config, operation['name'], True)


def create_network(compute, config):
    print 'Create network'
    body = {
        'description': 'Cloudify network',
        'name': config['network']
    }
    operation = compute.networks().insert(project=config['project'],
                                          body=body).execute()
    wait_for_operation(compute, config, operation['name'], True)


def run(config):
    with open(config['service_account']) as f:
        account_data = json.load(f)
    credentials = SignedJwtAssertionCredentials(account_data['client_email'],
                                                account_data['private_key'],
                                                scope=config['scope'])
    http = httplib2.Http()
    credentials.authorize(http)
    compute = build('compute', 'v1', http=http)
    upload_agent_key(compute, config)
    create_network(compute, config)
    allow_http(compute, config)
    print 'Creating cloudify manager instance.'

    operation = create_instance(compute, config)
    wait_for_operation(compute, config, operation['name'])
    print ' Instance created. \n ' \
          'It will take a minute or two for the instance to complete work.'


def find_and_replace(file_name, replace):
    with open(file_name, 'r+') as f:
        script = f.read()
        for item in replace:
            script = script.replace(item, replace[item])
        f.seek(0)
        f.write(script)
        f.truncate()


def prepare_startup_script(config):
    with open(config['ssh_key_private'], 'r') as f:
        ssh_private = f.read()
    with open(config['ssh_key_public'], 'r') as f:
        ssh_public = f.read()
    replace = {
        'USER=default': "USER={0}".format(config['agent_user']),
        'SSH_PRIVATE_KEY': ssh_private,
        'SSH_PUBLIC_KEY': ssh_public
    }
    find_and_replace('startup-script.sh', replace)


def upload_agent_key(compute, config):
    with open(config['ssh_key_public'], 'r') as f:
        ssh_public = f.read()
    metadata = {'items': [
        {'key': 'sshKeys',
         'value': '{0}: {1}'.format(config['agent_user'], ssh_public)}
    ], 'kind': 'compute#metadata'}
    compute.projects().setCommonInstanceMetadata(project=config['project'],
                                                 body=metadata).execute()


def main():
    with open(CONFIG) as f:
        config = yaml.safe_load(f).get('config')
        prepare_startup_script(config)
        run(config)


if __name__ == '__main__':
    main()