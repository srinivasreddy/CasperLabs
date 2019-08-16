from test.cl_node.common import (
    BONDING_CONTRACT,
    UNBONDING_CONTRACT,
    STANDARD_PAYMENT_CONTRACT,
)
from test.cl_node.client_parser import parse_show_block
from test.cl_node.client_parser import parse_show_blocks
from test.cl_node.casperlabs_network import OneNodeNetwork, PaymentNodeNetwork
from typing import List
from test.cl_node.casperlabs_accounts import GENESIS_ACCOUNT, Account
from test.cl_node.wait import wait_for_block_hash_propagated_to_all_nodes


def bond_to_the_network(network: OneNodeNetwork, contract: str, bond_amount: int):
    network.add_new_node_to_network()
    assert len(network.docker_nodes) == 2, "Total number of nodes should be 2."
    node0, node1 = network.docker_nodes
    block_hash = node1.bond(
        session_contract=contract, payment_contract=contract, bonding_amount=bond_amount
    )
    return block_hash


def assert_pre_state_of_network(network: OneNodeNetwork, stakes: List[int]):
    node0 = network.docker_nodes[0]
    blocks = parse_show_blocks(node0.client.show_blocks(1000))
    assert len(blocks) == 1
    genesis_block = blocks[0]
    item = list(
        filter(
            lambda x: x.stake in stakes
            and x.validator_public_key == node0.from_address,
            genesis_block.summary.header.state.bonds,
        )
    )
    assert len(item) == 0


def test_bonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: Bonding a validator node to an existing network.
    """
    bonding_amount = 1
    assert_pre_state_of_network(one_node_network_fn, [bonding_amount])
    block_hash = bond_to_the_network(
        one_node_network_fn, BONDING_CONTRACT, bonding_amount
    )
    node0, node1 = one_node_network_fn.docker_nodes
    assert block_hash is not None
    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is False
    assert r.error_message == ""

    block1 = node1.client.show_block(block_hash)
    block_ds = parse_show_block(block1)
    public_key = node1.genesis_account.public_key_hex
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1


def test_double_bonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: Bonding a validator node twice to an existing network.
    """
    bonding_amount = 1
    stakes = [1, 2]
    assert_pre_state_of_network(one_node_network_fn, stakes)
    block_hash = bond_to_the_network(
        one_node_network_fn, BONDING_CONTRACT, bonding_amount
    )
    assert block_hash is not None
    node1 = one_node_network_fn.docker_nodes[1]
    block_hash = node1.bond(
        session_contract=BONDING_CONTRACT,
        payment_contract=BONDING_CONTRACT,
        bonding_amount=bonding_amount,
    )
    assert block_hash is not None
    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is False
    assert r.error_message == ""

    node1 = one_node_network_fn.docker_nodes[1]
    block1 = node1.client.show_block(block_hash)
    block_ds = parse_show_block(block1)
    public_key = node1.genesis_account.public_key_hex
    item = list(
        filter(
            lambda x: x.stake == bonding_amount + bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1


def test_invalid_bonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: Bonding a validator node to an existing network.
    """
    # 190 is current total staked amount.
    bonding_amount = (190 * 1000) + 1
    block_hash = bond_to_the_network(
        one_node_network_fn, BONDING_CONTRACT, bonding_amount
    )
    assert block_hash is not None
    node1 = one_node_network_fn.docker_nodes[1]
    block1 = node1.client.show_block(block_hash)

    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is True
    assert r.error_message == "Exit code: 5"

    block_ds = parse_show_block(block1)
    public_key = node1.genesis_account.public_key_hex
    item = list(
        filter(
            lambda x: x.stake == bonding_amount
            and x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 0


def test_unbonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: unbonding a bonded validator node from an existing network.
    """
    bonding_amount = 1
    assert_pre_state_of_network(one_node_network_fn, [bonding_amount])
    block_hash = bond_to_the_network(
        one_node_network_fn, BONDING_CONTRACT, bonding_amount
    )
    assert block_hash is not None
    node1 = one_node_network_fn.docker_nodes[1]
    public_key = node1.genesis_account.public_key_hex
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


def test_partial_amount_unbonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: unbonding a bonded validator node with partial bonding amount from an existing network.
    """
    bonding_amount = 11
    unbond_amount = 4
    assert_pre_state_of_network(
        one_node_network_fn,
        [bonding_amount, unbond_amount, bonding_amount - unbond_amount],
    )
    block_hash = bond_to_the_network(
        one_node_network_fn, BONDING_CONTRACT, bonding_amount
    )
    assert block_hash is not None
    node1 = one_node_network_fn.docker_nodes[1]
    public_key = node1.genesis_account.public_key_hex
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


def test_invalid_unbonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: unbonding a bonded validator node from an existing network.
    """
    bonding_amount = 2000
    assert_pre_state_of_network(one_node_network_fn, [bonding_amount])
    block_hash = bond_to_the_network(
        one_node_network_fn, BONDING_CONTRACT, bonding_amount
    )
    assert block_hash is not None
    node1 = one_node_network_fn.docker_nodes[1]
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
            and x.validator_public_key == node1.genesis_account.public_key_hex,
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
            and x.validator_public_key == node1.genesis_account.public_key_hex,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1


def test_unbonding_without_bonding(one_node_network_fn):
    """
    Feature file: consensus.feature
    Scenario: unbonding a validator node which was not bonded to an existing network.
    """
    bonding_amount = 1
    assert_pre_state_of_network(one_node_network_fn, [bonding_amount])
    one_node_network_fn.add_new_node_to_network()
    assert (
        len(one_node_network_fn.docker_nodes) == 2
    ), "Total number of nodes should be 2."
    node0, node1 = one_node_network_fn.docker_nodes[:2]
    public_key = node1.genesis_account.public_key_hex
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


def test_node_makes_a_block_after_bonding(trillion_payment_node_network):
    """
    Feature file: consensus.feature
    Scenario: After bonding to a network node makes a block and unbonds the network, and makes an another block.
    """
    network: PaymentNodeNetwork = trillion_payment_node_network
    bonding_amount = 1
    assert_pre_state_of_network(network, [bonding_amount])
    network.add_new_node_to_network()
    assert len(network.docker_nodes) == 2, "Total number of nodes should be 2."
    node0, node1 = network.docker_nodes[:2]
    public_key = node1.genesis_account.public_key_hex
    block_hash = node1.bond(
        from_account_id="genesis",
        bonding_amount=bonding_amount,
        session_contract=BONDING_CONTRACT,
        payment_contract=STANDARD_PAYMENT_CONTRACT,
        payment_args_amount=10 ** 9,
    )

    assert block_hash is not None
    r = node1.client.show_deploys(block_hash)[0]
    assert r.is_error is False
    assert r.error_message == ""

    block2 = node1.client.show_block(block_hash)
    block_ds = parse_show_block(block2)
    item = list(
        filter(
            lambda x: x.validator_public_key == public_key,
            block_ds.summary.header.state.bonds,
        )
    )
    assert len(item) == 1
    to_account = Account(1)
    ABI = node0.p_client.abi
    session_args = ABI.args(
        [ABI.account(to_account.public_key_binary), ABI.u32(10 ** 6)]
    )
    payment_args = ABI.args([ABI.u512(10 ** 6)])

    _, deploy_hash_bytes = node1.p_client.deploy(
        from_address=GENESIS_ACCOUNT.public_key_hex,
        session_contract="transfer_to_account.wasm",
        payment_contract="standard_payment.wasm",
        public_key=GENESIS_ACCOUNT.public_key_path,
        private_key=GENESIS_ACCOUNT.private_key_path,
        session_args=session_args,
        payment_args=payment_args,
    )
    response = node1.p_client.propose()
    block_hash2 = response.block_hash.hex()
    wait_for_block_hash_propagated_to_all_nodes(network.docker_nodes, block_hash2)
    # Unbond operation should succeed but fails with No New Deploys
    block_hash3 = node1.unbond(
        session_contract=UNBONDING_CONTRACT,
        payment_contract=STANDARD_PAYMENT_CONTRACT,
        from_account_id="genesis",
        maybe_amount=None,
        payment_args_amount=10 ** 9,
    )
    r = node1.client.show_deploys(block_hash3)[0]
    assert r.is_error is False
    assert r.error_message == ""
    wait_for_block_hash_propagated_to_all_nodes(network.docker_nodes, block_hash3)
