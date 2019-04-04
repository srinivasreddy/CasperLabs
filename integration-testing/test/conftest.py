import sys
sys.path.insert(0, '.')


import contextlib
import dataclasses
import os
import random
import shutil
from typing import TYPE_CHECKING, Generator, List

import docker as docker_py
import pytest

from casperlabsnode_testing.common import KeyPair, TestingContext
from casperlabsnode_testing.pregenerated_keypairs import PREGENERATED_KEYPAIRS


if TYPE_CHECKING:
    from docker.client import DockerClient
    from _pytest.config.argparsing import Parser


@dataclasses.dataclass
class CommandLineOptions:
    peer_count: int
    node_startup_timeout: int
    network_converge_timeout: int
    receive_timeout: int
    command_timeout: int
    blocks: int
    mount_dir: str


def pytest_addoption(parser: "Parser") -> None:
    parser.addoption("--peer-count", action="store", default="2", help="number of peers in the network (excluding bootstrap node)")
    parser.addoption("--start-timeout", action="store", default="0", help="timeout in seconds for starting a node. Defaults to 30 + peer_count * 10")
    parser.addoption("--converge-timeout", action="store", default="0", help="timeout in seconds for network converge. Defaults to 200 + peer_count * 10")
    parser.addoption("--receive-timeout", action="store", default="0", help="timeout in seconds for receiving a message. Defaults to 10 + peer_count * 10")
    parser.addoption("--command-timeout", action="store", default="10", help="timeout in seconds for executing an casperlabsnode call (Examples: propose, show-logs etc.). Defaults to 10s")
    parser.addoption("--blocks", action="store", default="1", help="the number of deploys per test deploy")
    parser.addoption("--mount-dir", action="store", default=None, help="globally accesible directory for mounting between containers")


def make_timeout(peer_count: int, value: int, base: int, peer_factor: int = 10) -> int:
    if value > 0:
        return value
    return base + peer_count * peer_factor


@pytest.yield_fixture(scope='session')
def command_line_options_fixture(request):
    peer_count = int(request.config.getoption("--peer-count"))
    start_timeout = int(request.config.getoption("--start-timeout"))
    converge_timeout = int(request.config.getoption("--converge-timeout"))
    receive_timeout = int(request.config.getoption("--receive-timeout"))
    command_timeout = int(request.config.getoption("--command-timeout"))
    blocks = int(request.config.getoption("--blocks"))
    mount_dir = request.config.getoption("--mount-dir")

    command_line_options = CommandLineOptions(
        peer_count=peer_count,
        node_startup_timeout=180,
        network_converge_timeout=make_timeout(peer_count, converge_timeout, 200, 10),
        receive_timeout=make_timeout(peer_count, receive_timeout, 10, 10),
        command_timeout=command_timeout,
        blocks=blocks,
        mount_dir=mount_dir,
    )

    yield command_line_options


@contextlib.contextmanager
def temporary_resources_genesis_folder(validator_keys: List[KeyPair]) -> Generator[str, None, None]:
    genesis_folder_path = "/tmp/resources/genesis"
    if not os.path.exists(genesis_folder_path):
        os.makedirs(genesis_folder_path)
    try:
        with open(os.path.join(genesis_folder_path, "bonds.txt"), "w", buffering=1) as f:
            for pair in validator_keys:
                bond = random.randint(1, 100)
                f.write("{} {}\n".format(pair.public_key, bond))
                with open(os.path.join("/tmp/resources/genesis/",
                                       "{}.sk".format(pair.public_key)), 'w', buffering=1) as _file:
                    _file.write("{}\n".format(pair.private_key))
        yield genesis_folder_path
    finally:
        pass


@pytest.yield_fixture(scope='session')
def docker_client_fixture() -> Generator["DockerClient", None, None]:
    docker_client = docker_py.from_env()
    try:
        yield docker_client
    finally:
        docker_client.volumes.prune()
        docker_client.networks.prune()


@contextlib.contextmanager
def testing_context(command_line_options_fixture, docker_client_fixture, bootstrap_keypair: KeyPair = None, peers_keypairs: List[KeyPair] = None) -> Generator[TestingContext, None, None]:
    if bootstrap_keypair is None:
        bootstrap_keypair = PREGENERATED_KEYPAIRS[0]
    if peers_keypairs is None:
        peers_keypairs = PREGENERATED_KEYPAIRS[1:]

    # Using pre-generated validator key pairs by casperlabsnode. We do this because warning below  with python generated keys
    # WARN  io.casperlabs.casper.Validate$ - CASPER: Ignoring block 2cb8fcc56e... because block creator 3641880481... has 0 weight
    validator_keys = [kp for kp in [bootstrap_keypair] + peers_keypairs[0: command_line_options_fixture.peer_count + 1]]
    with temporary_resources_genesis_folder(validator_keys) as genesis_folder:
        bonds_file = os.path.join(genesis_folder, "bonds.txt")
        peers_keypairs = validator_keys
        context = TestingContext(
            bonds_file=bonds_file,
            bootstrap_keypair=bootstrap_keypair,
            peers_keypairs=peers_keypairs,
            docker=docker_client_fixture,
            **dataclasses.asdict(command_line_options_fixture),
        )

        yield context
