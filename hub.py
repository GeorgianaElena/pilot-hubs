from copy import deepcopy
import hashlib
import subprocess
import hmac
import os
import json
import tempfile
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError
from build import last_modified_commit
from contextlib import contextmanager
from build import build_image

# Without `pure=True`, I get an exception about str / byte issues
yaml = YAML(typ='safe', pure=True)

@contextmanager
def decrypt_file(encrypted_path):
    """
    Provide secure temporary decrypted contents of a given file

    If file isn't a sops encrypted file, we assume no encryption is used
    and return the current path.
    """
    # We must first determine if the file is using sops
    # sops files are JSON/YAML with a `sops` key. So we first check
    # if the file is valid JSON/YAML, and then if it has a `sops` key
    with open(encrypted_path) as f:
        _, ext = os.path.splitext(encrypted_path)
        # Support the (clearly wrong) people who use .yml instead of .yaml
        if ext == '.yaml' or ext == '.yml':
            try:
                encrypted_data = yaml.load(f)
            except ScannerError:
                yield encrypted_path
                return
        elif ext == '.json':
            try:
                encrypted_data = json.load(f)
            except json.JSONDecodeError:
                yield encrypted_path
                return

    if 'sops' not in encrypted_data:
        yield encrypted_path
        return

    # If file has a `sops` key, we assume it's sops encrypted
    with tempfile.NamedTemporaryFile() as f:
        subprocess.check_call([
            'sops',
            '--output', f.name,
            '--decrypt', encrypted_path
        ])
        yield f.name

class Cluster:
    """
    A single k8s cluster we can deploy to
    """
    def __init__(self, spec):
        self.spec = spec
        self.hubs = [
            Hub(self, hub_yaml)
            for hub_yaml in self.spec['hubs']
        ]

    def build_image(self):
        build_image(self.spec['image_repo'])

    @contextmanager
    def auth(self):
        with tempfile.NamedTemporaryFile() as kubeconfig:
            # FIXME: This is dumb
            os.environ['KUBECONFIG'] = kubeconfig.name
            assert self.spec['provider'] == 'gcp'

            yield from self.auth_gcp()

    def auth_gcp(self):
        config = self.spec['gcp']
        key_path = config['key']
        project = config['project']
        zone = config['zone']
        cluster = config['cluster']

        with decrypt_file(key_path) as decrypted_key_path:
            subprocess.check_call([
                'gcloud', 'auth',
                'activate-service-account',
                '--key-file', os.path.abspath(decrypted_key_path)
            ])

        subprocess.check_call([
            'gcloud', 'container', 'clusters',
            f'--zone={zone}',
            f'--project={project}',
            'get-credentials', cluster
        ])

        yield


class Hub:
    """
    A single, deployable JupyterHub
    """
    def __init__(self, cluster, spec):
        self.cluster = cluster
        self.spec = spec

    def get_generated_config(self):
        """
        Generate config automatically for each hub

        Some config should be automatically set for all hubs based on
        spec in hubs.yaml. We generate them here.

        Shouldn't have anything secret here.
        """

        return {
            'nfsPVC': {
                'nfs': {
                    'shareName': f'/export/home-01/homes/{self.spec["name"]}'
                }
            },
            'jupyterhub': {
                'ingress': {
                    'hosts': [self.spec['domain']],
                    'tls': [
                        {
                            'secretName': f'https-auto-tls',
                            'hosts': [self.spec['domain']]
                        }
                    ]

                },
                'singleuser': {
                    'image': {
                        'name': self.cluster.spec['image_repo']
                    },
                },
                'hub': {
                    'extraaEnv': {
                        'OAUTH_CALLBACK_URL': f'https://{self.spec["domain"]}/hub/oauth_callback'
                    }
                }
            }
        }

    def deploy(self, auth_provider, proxy_secret_key):
        client = auth_provider.ensure_client(
            self.spec['name'],
            self.spec['domain'],
            self.spec['auth0']['connection']
        )

        proxy_secret = hmac.new(proxy_secret_key, self.spec['name'].encode(), hashlib.sha256).hexdigest()
        secret_values = {
            'jupyterhub':  {
                'proxy': {
                    'secretToken': proxy_secret
                },
                'auth': auth_provider.get_client_creds(client, self.spec['auth0']['connection'])
            }
        }

        generated_values = self.get_generated_config()
        with tempfile.NamedTemporaryFile() as values_file, tempfile.NamedTemporaryFile() as generated_values_file, tempfile.NamedTemporaryFile() as secret_values_file:
            yaml.dump(self.spec['config'], values_file)
            yaml.dump(generated_values, generated_values_file)
            yaml.dump(secret_values, secret_values_file)
            values_file.flush()
            generated_values_file.flush()
            secret_values_file.flush()
            cmd = [
                'helm', 'upgrade', '--install', '--create-namespace', '--wait',
                '--namespace', self.spec['name'],
                self.spec['name'], 'hub',
                '-f', values_file.name,
                '-f', generated_values_file.name,
                '-f', secret_values_file.name
            ]
            print(f"Running {' '.join(cmd)}")
            subprocess.check_call(cmd)

