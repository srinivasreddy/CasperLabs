from . import conftest
from .cl_node.casperlabsnode import (
    CONTRACT_NAME,
    complete_network,
    deploy_and_propose,
)
from .cl_node.pregenerated_keypairs import PREGENERATED_KEYPAIRS


def test_persistent_dag_store(command_line_options_fixture, docker_client_fixture):
    with conftest.testing_context(
        command_line_options_fixture,
        docker_client_fixture,
        # Creates network with 1 bootstrap + 2 peer nodes
        peers_keypairs=PREGENERATED_KEYPAIRS[1:2]
    ) as context:
        with complete_network(context) as network:
            for node in network.nodes:
                deploy_and_propose(node, CONTRACT_NAME)
            node0 = network.peers[0]
            engine0 = network.engines[0]
            engine0.stop()
            node0.container.stop()
            deploy_and_propose(network.peers[1], CONTRACT_NAME)
            engine0.start()
            node0.container.start()