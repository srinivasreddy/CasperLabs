import threading
from random import Random

from docker.client import DockerClient

import conftest
from  conftest import (
    CommandLineOptions
)
from casperlabsnode_testing.common import (
    random_string
)

from casperlabsnode_testing.casperlabsnode import (
    Node,
    bootstrap_connected_peer,
    docker_network_with_started_bootstrap,
)
from casperlabsnode_testing.wait import (
    wait_for_peers_count_at_least,
    wait_for_blocks_count_at_least,
)


class DeployThread(threading.Thread):
    def __init__(self, name: str, node: Node, contract: str, count: int) -> None:
        threading.Thread.__init__(self)
        self.name = name
        self.node = node
        self.contract = contract
        self.count = count

    def run(self) -> None:
        for _ in range(self.count):
            self.node.deploy(self.contract)
            self.node.propose()


BOOTSTRAP_NODE_KEYS = conftest.KeyPair(private_key='80366db5fbb8dad7946f27037422715e4176dda41d582224db87b6c3b783d709', public_key='1cd8bf79a2c1bd0afa160f6cdfeb8597257e48135c9bf5e4823f2875a1492c97')
BONDED_VALIDATOR_KEY_1 = conftest.KeyPair(private_key='120d42175739387af0264921bb117e4c4c05fbe2ce5410031e8b158c6e414bb5', public_key='02ab69930f74b931209df3ce54e3993674ab3e7c98f715608a5e74048b332821')
BONDED_VALIDATOR_KEY_2 = conftest.KeyPair(private_key='f7bfb2b3f2be909dd50beac05bece5940b1e7266816d7294291a2ff66a5d660b', public_key='00be417b7d7032bf742dac491ea3318a757e7420ca313afa2862147ac41f8df9')
BONDED_VALIDATOR_KEY_3 = conftest.KeyPair(private_key='2b173084083291ac6850cb734dffb69dfcb280aeb152f0d5be979bea7827c03a', public_key='017f286d499ab1d4a43a0b2efed6f12935e273fb6027daefa1959a8953354d77')


def test_multiple_deploys_at_once(command_line_options_fixture, docker_client_fixture):
    contract_path = 'helloname.wasm'
    peers_keypairs = [BONDED_VALIDATOR_KEY_1, BONDED_VALIDATOR_KEY_2, BONDED_VALIDATOR_KEY_3]
    with conftest.testing_context(command_line_options_fixture, docker_client_fixture, bootstrap_keypair=BOOTSTRAP_NODE_KEYS, peers_keypairs=peers_keypairs) as context:
        with docker_network_with_started_bootstrap(context=context) as bootstrap_node:
            volume_name = "casperlabs{}".format(random_string(5).lower())
            docker_client_fixture.volumes.create(name=volume_name, driver="local")
            kwargs = {'context': context, 'bootstrap': bootstrap_node, 'socket_volume': volume_name}
            with bootstrap_connected_peer(name='bonded-validator-1', keypair=BONDED_VALIDATOR_KEY_1, **kwargs) as no1:
                with bootstrap_connected_peer(name='bonded-validator-2', keypair=BONDED_VALIDATOR_KEY_2, **kwargs) as no2:
                    with bootstrap_connected_peer(name='bonded-validator-3', keypair=BONDED_VALIDATOR_KEY_3, **kwargs) as no3:
                        wait_for_peers_count_at_least(bootstrap_node, 3, context.node_startup_timeout)

                        deploy1 = DeployThread("node1", no1, contract_path, 1)
                        deploy1.start()

                        expected_blocks_count = 1
                        wait_for_blocks_count_at_least(
                            no1,
                            expected_blocks_count,
                            3,
                            context.node_startup_timeout
                        )

                        deploy2 = DeployThread("node2", no2, contract_path, 3)
                        deploy2.start()

                        deploy3 = DeployThread("node3", no3, contract_path, 3)
                        deploy3.start()

                        expected_blocks_count = 7
                        wait_for_blocks_count_at_least(
                            no1,
                            expected_blocks_count,
                            3,
                            context.node_startup_timeout
                        )
                        wait_for_blocks_count_at_least(
                            no2,
                            expected_blocks_count,
                            3,
                            context.node_startup_timeout
                        )
                        wait_for_blocks_count_at_least(
                            no3,
                            expected_blocks_count,
                            3,
                            context.node_startup_timeout
                        )

                        deploy1.join()
                        deploy2.join()
                        deploy3.join()

    for _volume in docker_client_fixture.volumes.list():
        if _volume.name.startswith("casperlabs"):
            _volume.remove()