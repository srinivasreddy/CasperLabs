from test.cl_node.casperlabsnode import BONDING_CONTRACT, UNBONDING_CONTRACT
from test.cl_node.client_parser import parse_show_block
from test.cl_node.client_parser import parse_show_blocks
from test.cl_node.casperlabs_network import OneNodeNetwork, Account

import pytest


def bond_to_the_network(
    network: OneNodeNetwork, contract: str, bond_amount: int, account: Account
):
    assert len(network.docker_nodes) == 2, "Total number of nodes should be 2."
    node0, node1 = network.docker_nodes
    block_hash = node1.bond(
        session_contract=contract,
        payment_contract=contract,
        amount=bond_amount,
        from_account=account,
    )
    return block_hash


def assert_pre_state_of_network(network: OneNodeNetwork, stake: int, account: Account):
    node1 = network.docker_nodes[1]
    blocks = parse_show_blocks(node1.client.show_blocks(1000))
    genesis_block = blocks[0]
    item = list(
        filter(
            lambda x: x.stake == stake
            and x.validator_public_key == account.public_key_hex,
            genesis_block.summary.header.state.bonds,
        )
    )
    assert len(item) == 0


def test_bonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: Bonding a validator node to an existing network.
    """
    bonding_amount = 1
    one_node_network.add_new_node_to_network()
    node1 = one_node_network.docker_nodes[1]
    account = node1.test_account
    assert_pre_state_of_network(one_node_network, bonding_amount, account)
    block_hash = bond_to_the_network(
        one_node_network, BONDING_CONTRACT, bonding_amount, account
    )
    assert block_hash is not None
    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is False
    assert r.error_message == ""

    block1 = node1.client.show_block(block_hash)
    block_ds = parse_show_block(block1)
    public_key = account.public_key_hex
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1
    one_node_network.stop_cl_node(1)


def test_double_bonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: Bonding a validator node twice to an existing network.
    """
    bonding_amount = 1
    one_node_network.add_new_node_to_network()
    node0, node1 = one_node_network.docker_nodes
    account = node1.test_account
    assert_pre_state_of_network(one_node_network, bonding_amount, account)

    block_hash = bond_to_the_network(
        one_node_network, BONDING_CONTRACT, bonding_amount, account
    )
    assert block_hash is not None
    block_hash = node1.bond(
        session_contract=BONDING_CONTRACT,
        payment_contract=BONDING_CONTRACT,
        amount=bonding_amount,
        from_account=account,
    )
    assert block_hash is not None
    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is False
    assert r.error_message == ""

    block1 = node1.client.show_block(block_hash)
    block_ds = parse_show_block(block1)
    public_key = account.public_key_hex
    item = list(
        filter(
            lambda x: x.stake == bonding_amount + bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1
    one_node_network.stop_cl_node(1)


@pytest.mark.skip()
def test_invalid_bonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: Bonding a validator node to an existing network.
    """
    # 190 is current total staked amount.
    bonding_amount = (190 * 1000) + 1
    one_node_network.add_new_node_to_network()
    node0, node1 = one_node_network.docker_nodes
    account = node1.test_account
    block_hash = bond_to_the_network(
        one_node_network, BONDING_CONTRACT, bonding_amount, account
    )
    assert block_hash is not None
    node1 = one_node_network.docker_nodes[1]
    block1 = node1.client.show_block(block_hash)

    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is True
    assert r.error_message == "Exit code: 5"

    block_ds = parse_show_block(block1)
    public_key = node1.test_account.public_key_hex
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 0


@pytest.mark.skip()
def test_unbonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: unbonding a bonded validator node from an existing network.
    """
    bonding_amount = 1
    assert_pre_state_of_network(one_node_network, [bonding_amount])
    block_hash = bond_to_the_network(one_node_network, BONDING_CONTRACT, bonding_amount)
    assert block_hash is not None
    node1 = one_node_network.docker_nodes[1]
    public_key = node1.test_account.public_key_hex
    block_hash2 = node1.unbond(
        session_contract=UNBONDING_CONTRACT,
        payment_contract=UNBONDING_CONTRACT,
        maybe_amount=None,
    )

    assert block_hash2 is not None
    r = node1.client.show_deploys(block_hash2)[0]
    assert r.is_error is False
    assert r.error_message == ""

    block2 = node1.client.show_block(block_hash2)
    block_ds = parse_show_block(block2)
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 0


@pytest.mark.skip()
def test_partial_amount_unbonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: unbonding a bonded validator node with partial bonding amount from an existing network.
    """
    bonding_amount = 11
    unbond_amount = 4
    assert_pre_state_of_network(
        one_node_network,
        [bonding_amount, unbond_amount, bonding_amount - unbond_amount],
    )
    block_hash = bond_to_the_network(one_node_network, BONDING_CONTRACT, bonding_amount)
    assert block_hash is not None
    node1 = one_node_network.docker_nodes[1]
    public_key = node1.test_account.public_key_hex
    block_hash2 = node1.unbond(
        session_contract=UNBONDING_CONTRACT,
        payment_contract=UNBONDING_CONTRACT,
        maybe_amount=unbond_amount,
    )

    r = node1.client.show_deploys(block_hash2)[0]
    assert r.is_error is False
    assert r.error_message == ""

    assert block_hash2 is not None
    block2 = node1.client.show_block(block_hash2)
    block_ds = parse_show_block(block2)
    item = list(
        filter(
            lambda x: x.stake == bonding_amount - unbond_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1


@pytest.mark.skip()
def test_invalid_unbonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: unbonding a bonded validator node from an existing network.
    """
    bonding_amount = 2000
    assert_pre_state_of_network(one_node_network, [bonding_amount])
    block_hash = bond_to_the_network(one_node_network, BONDING_CONTRACT, bonding_amount)
    assert block_hash is not None
    node1 = one_node_network.docker_nodes[1]
    block_hash2 = node1.unbond(
        session_contract=UNBONDING_CONTRACT,
        payment_contract=UNBONDING_CONTRACT,
        maybe_amount=1985,  # 1985 > (2000+190) * 0.9
    )

    assert block_hash2 is not None
    r = node1.client.show_deploys(block_hash2)[0]
    assert r.is_error is True
    assert r.error_message == "Exit code: 6"
    block2 = node1.client.show_block(block_hash2)
    block_ds = parse_show_block(block2)
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == node1.test_account.public_key_hex,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1

    block_hash2 = node1.unbond(
        session_contract=UNBONDING_CONTRACT,
        payment_contract=UNBONDING_CONTRACT,
        maybe_amount=None,
    )
    assert block_hash2 is not None
    r = node1.client.show_deploys(block_hash2)[0]
    assert r.is_error is True
    assert r.error_message == "Exit code: 6"
    block2 = node1.client.show_block(block_hash2)
    block_ds = parse_show_block(block2)
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == node1.test_account.public_key_hex,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1


@pytest.mark.skip()
def test_unbonding_without_bonding(one_node_network):
    """
    Feature file: consensus.feature
    Scenario: unbonding a validator node which was not bonded to an existing network.
    """
    bonding_amount = 1
    assert_pre_state_of_network(one_node_network, [bonding_amount])
    one_node_network.add_new_node_to_network()
    assert len(one_node_network.docker_nodes) == 2, "Total number of nodes should be 2."
    node0, node1 = one_node_network.docker_nodes[:2]
    public_key = node1.test_account.public_key_hex
    block_hash = node1.unbond(
        session_contract=UNBONDING_CONTRACT,
        payment_contract=UNBONDING_CONTRACT,
        maybe_amount=None,
    )

    assert block_hash is not None
    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is True
    assert r.error_message == "Exit code: 0"

    block2 = node1.client.show_block(block_hash)
    block_ds = parse_show_block(block2)
    item = list(
        filter(
            lambda x: x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 0
