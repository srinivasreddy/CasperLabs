import contextlib
import logging
import os
import random
import re
import shlex
import string
import threading
from multiprocessing import Process, Queue
from queue import Empty
from typing import TYPE_CHECKING, Dict, Generator, List, Optional, Tuple, Union

import pytest
from docker.client import DockerClient
from docker.errors import ContainerError

import conftest
from casperlabsnode_testing.common import (
    TestingContext,
    make_tempdir,
    random_string,
)
from casperlabsnode_testing.wait import wait_for_node_started


if TYPE_CHECKING:
    from conftest import KeyPair
    from docker.models.containers import Container
    from logging import Logger
    from threading import Event

TAG = os.environ.get(
    "DRONE_BUILD_NUMBER",
    None)
if TAG is None:
    TAG = "test"
else:
    TAG = "DRONE-" + TAG
DEFAULT_IMAGE = os.environ.get(
    "DEFAULT_IMAGE",
    "casperlabs/node:{}".format(TAG))

DEFAULT_ENGINE_IMAGE = "casperlabs/execution-engine:test"

casperlabsnode_binary = '/opt/docker/bin/bootstrap'
casperlabsnode_directory = "/root/.casperlabs"
casperlabsnode_deploy_dir = "{}/deploy".format(casperlabsnode_directory)
casperlabsnode_genesis_folder = '{}/genesis/'.format(casperlabsnode_directory)
casperlabsnode_bonds_file = '{}/genesis/bonds.txt'.format(casperlabsnode_directory)
casperlabsnode_certificate = '{}/node.certificate.pem'.format(casperlabsnode_directory)
casperlabsnode_key = '{}/node.key.pem'.format(casperlabsnode_directory)
casperlabsnode_sockets = '{}/sockets'.format(casperlabsnode_directory)
casperlabsnode_bootstrap_folder = "{}/bootstrap".format(casperlabsnode_directory)

grpc_socket_file = "{}/.casper-node.sock".format(casperlabsnode_sockets)
execution_engine_command = ".casperlabs/sockets/.casper-node.sock"


class InterruptedException(Exception):
    pass


class CasperLabsNodeAddressNotFoundError(Exception):
    pass


class NonZeroExitCodeError(Exception):
    def __init__(self, command: Tuple[Union[int, str], ...], exit_code: int, output: str):
        self.command = command
        self.exit_code = exit_code
        self.output = output

    def __repr__(self) -> str:
        return '{}({}, {}, {})'.format(
            self.__class__.__name__,
            repr(self.command),
            self.exit_code,
            repr(self.output),
        )


class TimeoutError(Exception):
    def __init__(self, command: Union[Tuple[str, ...], str], timeout: int) -> None:
        self.command = command
        self.timeout = timeout


class UnexpectedShowBlocksOutputFormatError(Exception):
    def __init__(self, output: str) -> None:
        self.output = output


class UnexpectedProposeOutputFormatError(Exception):
    def __init__(self, output: str) -> None:
        self.output = output


def extract_block_count_from_show_blocks(show_blocks_output: str) -> int:
    lines = show_blocks_output.splitlines()
    prefix = 'count: '
    interesting_lines = [l for l in lines if l.startswith(prefix)]
    if len(interesting_lines) != 1:
        raise UnexpectedShowBlocksOutputFormatError(show_blocks_output)
    line = interesting_lines[0]
    count = line[len(prefix):]
    try:
        result = int(count)
    except ValueError:
        raise UnexpectedShowBlocksOutputFormatError(show_blocks_output)
    return result


def extract_block_hash_from_propose_output(propose_output: str):
    """We're getting back something along the lines of:

    Response: Success! Block a91208047c... created and added.\n
    """
    match = re.match(r'Response: Success! Block ([0-9a-f]+)\.\.\. created and added.', propose_output.strip())
    if match is None:
        raise UnexpectedProposeOutputFormatError(propose_output)
    return match.group(1)


class Node:
    def __init__(self, container: "Container", deploy_dir: str, docker_client: "DockerClient", timeout: int,
                 network: str, volume: str) -> None:
        self.container = container
        self.local_deploy_dir = deploy_dir
        self.remote_deploy_dir = casperlabsnode_deploy_dir
        self.name = container.name
        self.docker_client = docker_client
        self.timeout = timeout
        self.network = network
        self.volume = volume
        self.terminate_background_logging_event = threading.Event()
        self.background_logging = LoggingThread(
            container=container,
            logger=logging.getLogger('peers'),
            terminate_thread_event=self.terminate_background_logging_event,
        )
        self.background_logging.start()

    def __repr__(self) -> str:
        return '<Node(name={})>'.format(repr(self.name))

    def logs(self) -> str:
        return self.container.logs().decode('utf-8')

    def get_casperlabsnode_address(self) -> str:
        log_content = self.logs()
        m = re.search("Listening for traffic on (casperlabs://.+@{name}\\?protocol=\\d+&discovery=\\d+)\\.$".format(name=self.container.name),
                      log_content,
                      re.MULTILINE | re.DOTALL)
        if m is None:
            raise CasperLabsNodeAddressNotFoundError()
        address = m.group(1)
        return address

    def get_metrics(self) -> Tuple[int, str]:
        cmd = 'curl -s http://localhost:40403/metrics'
        output = self.exec_run(cmd=cmd)
        return output

    def get_metrics_strict(self):
        output = self.shell_out('curl', '-s', 'http://localhost:40403/metrics')
        return output

    def cleanup(self) -> None:
        self.container.remove(force=True, v=True)
        self.terminate_background_logging_event.set()
        self.background_logging.join()

    def deploy_contract(self, contract: str) -> Tuple[int, str]:
        cmd = '{casperlabsnode_binary} deploy --from "00000000000000000000" --gas-limit 1000000 --gas-price 1 --nonce 0 {casperlabsnode_deploy_dir}/{contract}'.format(
            casperlabsnode_binary=casperlabsnode_binary,
            casperlabsnode_deploy_dir=casperlabsnode_deploy_dir,
            contract=contract
        )
        return self.exec_run(cmd)

    def propose_contract(self) -> Tuple[int, str]:
        return self.exec_run('{} propose'.format(casperlabsnode_binary))

    def show_blocks(self) -> Tuple[int, str]:
        return self.exec_run('{} show-blocks'.format(casperlabsnode_binary))

    def show_blocks_with_depth(self, depth: int) -> str:
        command = " ".join([
            "--host",
            self.name,
            "show-blocks",
            "--depth={}".format(depth)
        ])
        return self.invoke_client(command)

    def get_blocks_count(self, depth: int) -> int:
        show_blocks_output = self.show_blocks_with_depth(depth)
        return extract_block_count_from_show_blocks(show_blocks_output)

    def get_block(self, block_hash: str) -> str:
        return self.call_casperlabsnode('show-block', block_hash, stderr=False)

    # deprecated, don't use, why? ask @adaszko
    def exec_run(self, cmd: Union[Tuple[str, ...], str], stderr=True) -> Tuple[int, str]:
        queue: Queue = Queue(1)

        def execution():
            r = self.container.exec_run(cmd, stderr=stderr)
            queue.put((r.exit_code, r.output.decode('utf-8')))

        process = Process(target=execution)

        logging.info("COMMAND {} {}".format(self.name, cmd))

        process.start()

        try:
            exit_code, output = queue.get(True, None)
            if exit_code != 0:
                logging.warning("EXITED {} {} {}".format(self.name, cmd, exit_code))
            logging.debug('OUTPUT {}'.format(repr(output)))
            return exit_code, output
        except Empty:
            process.terminate()
            process.join()
            raise TimeoutError(cmd, self.timeout)

    def shell_out(self, *cmd: str, stderr=True) -> str:
        exit_code, output = self.exec_run(cmd, stderr=stderr)
        if exit_code != 0:
            raise NonZeroExitCodeError(command=cmd, exit_code=exit_code, output=output)
        return output

    def call_casperlabsnode(self, *node_args: str, stderr: bool = True) -> str:
        return self.shell_out(casperlabsnode_binary, *node_args, stderr=stderr)

    def invoke_client(self, command: str, volumes: Dict[str, Dict[str, str]] = None) -> str:
        if volumes is None:
            volumes = {}
        try:
            logging.info("COMMAND {}".format(command))
            output = self.docker_client.containers.run(
                image="casperlabs/client:latest",
                auto_remove=True,
                name="client-{}-latest".format(
                    ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(5)),
                ),
                command=command,
                network=self.network,
                volumes=volumes
            ).decode('utf-8')
            logging.debug("OUTPUT {}".format(output))
            return output
        except ContainerError as err:
            logging.warning("EXITED code={} command='{}' stderr='{}'".format(err.exit_status, err.command, err.stderr))
            raise NonZeroExitCodeError(command=(command, err.exit_status), exit_code=err.exit_status, output=err.stderr)

    def deploy(self, from_address: str = "00000000000000000000",
               gas_limit: int = 1000000, gas_price: int = 1, nonce: int = 0) -> str:

        command = " ".join([
            "--host",
            self.name,
            "deploy",
            "--from", from_address,
            "--gas-limit", str(gas_limit),
            "--gas-price", str(gas_price),
            "--nonce", str(nonce),
            "--session=/data/helloname.wasm",
            "--payment=/data/helloname.wasm"
        ])

        volumes = {
            "/tmp/resources/": {
                "bind": "/data",
                "mode": "ro"
            }
        }
        return self.invoke_client(command, volumes)

    def deploy_string(self, rholang_code: str) -> str:
        quoted_rholang = shlex.quote(rholang_code)
        return self.shell_out('sh', '-c', 'echo {quoted_rholang} >/tmp/deploy_string.rho && {casperlabsnode_binary} deploy --phlo-limit=10000000000 --phlo-price=1 /tmp/deploy_string.rho'.format(
            casperlabsnode_binary=casperlabsnode_binary,
            quoted_rholang=quoted_rholang,
        ))

    def propose(self) -> str:
        command = " ".join([
            "--host",
            self.name,
            "propose"
        ])
        return self.invoke_client(command)

    def generate_faucet_bonding_deploys(self, bond_amount: int, private_key: str, public_key: str) -> str:
        return self.call_casperlabsnode('generateFaucetBondingDeploys',
                                        '--amount={}'.format(bond_amount),
                                        '--private-key={}'.format(private_key),
                                        '--public-key={}'.format(public_key),
                                        '--sig-algorithm=ed25519')

    def cat_forward_file(self, public_key: str) -> str:
        return self.shell_out('cat', '/opt/docker/forward_{}.rho'.format(public_key))

    def cat_bond_file(self, public_key: str) -> str:
        return self.shell_out('cat', '/opt/docker/bond_{}.rho'.format(public_key))

    __timestamp_rx = "\\d\\d:\\d\\d:\\d\\d\\.\\d\\d\\d"
    __log_message_rx = re.compile("^{timestamp_rx} (.*?)(?={timestamp_rx})".format(timestamp_rx=__timestamp_rx), re.MULTILINE | re.DOTALL)

    def log_lines(self) -> List[str]:
        log_content = self.logs()
        return Node.__log_message_rx.split(log_content)


class LoggingThread(threading.Thread):
    def __init__(self, terminate_thread_event: "Event", container: "Container", logger: "Logger") -> None:
        super().__init__()
        self.terminate_thread_event = terminate_thread_event
        self.container = container
        self.logger = logger

    def run(self) -> None:
        containers_log_lines_generator = self.container.logs(stream=True, follow=True)
        try:
            while True:
                if self.terminate_thread_event.is_set():
                    break
                line = next(containers_log_lines_generator)
                self.logger.info('\t{}: {}'.format(self.container.name, line.decode('utf-8').rstrip()))
        except StopIteration:
            pass


def make_container_command(container_command: str, container_command_options: Dict, is_bootstrap: bool):
    opts = ['{} {}'.format(option, argument) for option, argument in container_command_options.items()]
    if is_bootstrap:
        result = '{} -s {}'.format(container_command, ' '.join(opts))
    else:
        result = '{} {}'.format(container_command, ' '.join(opts))
    return result


def make_node(
    *,
    docker_client: "DockerClient",
    name: str,
    network: str,
    socket_volume: str,
    container_command: str,
    container_command_options: Dict,
    command_timeout: int,
    extra_volumes: Dict[str, Dict[str, str]],
    image: str = DEFAULT_IMAGE,
    mem_limit: Optional[str] = None,
    is_bootstrap: bool = False
) -> Node:
    assert isinstance(name, str)
    assert '_' not in name, 'Underscore is not allowed in host name'
    deploy_dir = make_tempdir(prefix="resources/")
    # end slash is necessary to create in format /tmp/resources/xxxxxxxx

    command = make_container_command(container_command, container_command_options, is_bootstrap)

    env = {
        'RUST_BACKTRACE': 'full'
    }
    java_options = os.environ.get('_JAVA_OPTIONS')
    if java_options is not None:
        env['_JAVA_OPTIONS'] = java_options
    logging.debug('Using _JAVA_OPTIONS: {}'.format(java_options))

    volumes = {
        deploy_dir: {
            "bind": casperlabsnode_deploy_dir,
            "mode": "rw"
        },
        socket_volume: {
            "bind": casperlabsnode_sockets,
            "mode": "rw"
        }
    }
    result_volumes = {}
    result_volumes.update(volumes)
    result_volumes.update(extra_volumes)
    container = docker_client.containers.run(
        image,
        name=name,
        user='root',
        auto_remove=False,
        detach=True,
        mem_limit=mem_limit,
        network=network,
        volumes=result_volumes,
        command=command,
        hostname=name,
        environment=env,
    )

    node = Node(
        container,
        deploy_dir,
        docker_client,
        command_timeout,
        network,
        socket_volume
    )

    return node


def get_absolute_path_for_mounting(relative_path: str, mount_dir: Optional[str] = None) -> str:
    """Drone runs each job in a new Docker container FOO. That Docker
    container has a new filesystem. Anything in that container can read
    anything in that filesystem. To read files from HOST, it has to be shared
    though, so let's share /tmp:/tmp. You also want to start new Docker
    containers, so you share /var/run/docker.sock:/var/run/docker.sock. When
    you start a new Docker container from FOO, it's not in any way nested. You
    just contact the Docker daemon running on HOST via the shared docker.sock.
    So when you start a new image from FOO, the HOST creates a new Docker
    container BAR with brand new filesystem. So if you tell Docker from FOO to
    mount /MOUNT_DIR:/MOUNT_DIR from FOO to BAR, the Docker daemon will actually mount
    /MOUNT_DIR from HOST to BAR, and not from FOO to BAR.
    """

    if mount_dir is not None:
        return os.path.join(mount_dir, relative_path)
    return os.path.abspath(os.path.join('resources', relative_path))


def make_bootstrap_node(
    *,
    docker_client: "DockerClient",
    network: str,
    socket_volume: str,
    key_pair: "KeyPair",
    command_timeout: int,
    mem_limit: Optional[str] = None,
    cli_options: Optional[Dict] = None,
    container_name: Optional[str] = None,
) -> Node:

    genesis_folder = "/tmp/resources/genesis"
    bootstrap_folder = "/tmp/resources/bootstrap_certificate"

    name = "{node_name}.{network_name}".format(
        node_name='bootstrap' if container_name is None else container_name,
        network_name=network,
    )
    container_command_options = {
        "--casper-validator-private-key": key_pair.private_key,
        "--casper-has-faucet": "",
        "--server-host": name,
        "--metrics-prometheus": "",
        "--server-data-dir=/root/.casperlabs": "",
        "--grpc-socket": grpc_socket_file
    }
    if cli_options is not None:
        container_command_options.update(cli_options)

    volumes = {
        bootstrap_folder: {
            "bind": casperlabsnode_bootstrap_folder,
            "mode": "rw"
        },
        genesis_folder: {
            "bind": casperlabsnode_genesis_folder,
            "mode": "rw"
        }
    }

    container = make_node(
        docker_client=docker_client,
        name=name,
        network=network,
        socket_volume=socket_volume,
        container_command='run',
        container_command_options=container_command_options,
        command_timeout=command_timeout,
        extra_volumes=volumes,
        mem_limit=mem_limit if mem_limit is not None else '4G',
        is_bootstrap=True
    )
    return container


def make_execution_engine(
    *,
    docker_client: "DockerClient",
    network: str,
    socket_volume: str,
    name: str,
    command: str,
    image: str = DEFAULT_ENGINE_IMAGE,
):
    name = make_engine_name(network, name)
    volumes = {
        socket_volume: {
            "bind": "/opt/docker/.casperlabs/sockets",
            "mode": "rw"
        }
    }
    container = docker_client.containers.run(
        image,
        name=name,
        user='root',
        detach=True,
        command=command,
        network=network,
        volumes=volumes,
        hostname=name,
    )
    return container


def make_peer_name(network: str, i: Union[int, str]) -> str:
    return "peer{i}.{network}".format(i=i, network=network)


def make_engine_name(network: str, i: Union[int, str]) -> str:
    return "engine{i}.{network}".format(i=i, network=network)


def make_peer(
    *,
    docker_client: "DockerClient",
    network: str,
    socket_volume: str,
    name: str,
    command_timeout: int,
    bootstrap: Node,
    key_pair: "KeyPair",
    mem_limit: Optional[str] = None,
) -> Node:
    assert isinstance(name, str)
    assert '_' not in name, 'Underscore is not allowed in host name'
    name = make_peer_name(network, name)
    bootstrap_address = bootstrap.get_casperlabsnode_address()

    container_command_options = {
        "--server-bootstrap": bootstrap_address,
        "--casper-validator-private-key": key_pair.private_key,
        "--server-host": name,
        "--metrics-prometheus": "",
        "--grpc-socket": grpc_socket_file
    }

    container = make_node(
        docker_client=docker_client,
        name=name,
        network=network,
        socket_volume=socket_volume,
        container_command='run',
        container_command_options=container_command_options,
        command_timeout=command_timeout,
        extra_volumes={},
        mem_limit=mem_limit if not None else '4G',
    )
    return container


@contextlib.contextmanager
def started_peer(
    *,
    context,
    network,
    socket_volume,
    name,
    bootstrap,
    key_pair,
):
    peer = make_peer(
        docker_client=context.docker,
        network=network,
        socket_volume=socket_volume,
        name=name,
        bootstrap=bootstrap,
        key_pair=key_pair,
        command_timeout=context.command_timeout,
    )
    try:
        wait_for_node_started(peer, context.node_startup_timeout)
        yield peer
    finally:
        peer.cleanup()


def create_peer_nodes(
    *,
    docker_client: "DockerClient",
    bootstrap: Node,
    network: str,
    key_pairs: List["KeyPair"],
    command_timeout: int,
    allowed_peers: Optional[List[str]] = None,
    allowed_engines: Optional[List[str]] = None,
    mem_limit: Optional[str] = None,
):
    assert len(set(key_pairs)) == len(key_pairs), "There shouldn't be any duplicates in the key pairs"

    if allowed_peers is None:
        allowed_peers = [bootstrap.name] + [make_peer_name(network, i) for i in range(0, len(key_pairs))]

    if allowed_engines is None:
        allowed_engines = ["enginebootstrap.{}".format(network)] + [make_engine_name(network, i) for i in range(0, len(key_pairs))]

    result = []
    execution_engines = []
    try:
        for i, key_pair in enumerate(key_pairs):
            volume_name = "casperlabs{}".format(random_string(5).lower())
            docker_client.volumes.create(name=volume_name, driver="local")
            engine = make_execution_engine(
                docker_client=docker_client,
                name=str(i),
                command=execution_engine_command,
                network=network,
                socket_volume=volume_name,
            )
            execution_engines.append(engine)

            peer_node = make_peer(
                docker_client=docker_client,
                network=network,
                socket_volume=volume_name,
                name=str(i),
                command_timeout=command_timeout,
                bootstrap=bootstrap,
                key_pair=key_pair,
                mem_limit=mem_limit if mem_limit is not None else '4G',
            )
            result.append(peer_node)
    except:
        for node in result:
            node.cleanup()
        for _engine in execution_engines:
            _engine.remove(force=True, v=True)
        for _volume in docker_client.volumes.list():
            if _volume.name.startswith("casperlabs"):
                _volume.remove()
        raise
    return result, execution_engines


@contextlib.contextmanager
def docker_network(docker_client: "DockerClient") -> Generator[str, None, None]:
    network_name = "casperlabs{}".format(random_string(5).lower())
    docker_client.networks.create(network_name, driver="bridge")
    try:
        yield network_name
    finally:
        for network in docker_client.networks.list():
            if network_name == network.name:
                network.remove()


@contextlib.contextmanager
def docker_volume(docker_client: "DockerClient") -> Generator[str, None, None]:
    volume_name = "casperlabs{}".format(random_string(5).lower())
    docker_client.volumes.create(name=volume_name, driver="local")
    try:
        yield volume_name
    finally:
        for volume in docker_client.volumes.list():
            if volume_name == volume.name:
                volume.remove()


@contextlib.contextmanager
def started_bootstrap_node(*, context: TestingContext, network: str, socket_volume: str, container_name: str = None) -> Generator[Node, None, None]:
    engine = make_execution_engine(
        docker_client=context.docker,
        name="bootstrap",
        command=execution_engine_command,
        network=network,
        socket_volume=socket_volume,
    )
    bootstrap_node = make_bootstrap_node(
        docker_client=context.docker,
        network=network,
        socket_volume=socket_volume,
        key_pair=context.bootstrap_keypair,
        command_timeout=context.command_timeout,
        container_name=container_name,
    )
    try:
        wait_for_node_started(bootstrap_node, context.node_startup_timeout)
        yield bootstrap_node
    finally:
        engine.remove(force=True, v=True)
        bootstrap_node.cleanup()


@contextlib.contextmanager
def docker_network_with_started_bootstrap(context, *, container_name=None):
    with docker_network(context.docker) as network:
        with docker_volume(context.docker) as volume:
            with started_bootstrap_node(context=context,
                                        network=network,
                                        socket_volume=volume,
                                        container_name=container_name) as node:
                yield node


@pytest.yield_fixture(scope='module')
def started_standalone_bootstrap_node(command_line_options_fixture, docker_client_fixture):
    with conftest.testing_context(command_line_options_fixture, docker_client_fixture) as context:
        with docker_network_with_started_bootstrap(context=context) as bootstrap_node:
            yield bootstrap_node
