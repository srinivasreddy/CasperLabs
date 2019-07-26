from test.cl_node.client_parser import parse_show_blocks
from test.cl_node.client_parser import parse_show_block
from test.cl_node.docker_node import DockerNode
from test.cl_node.casperlabs_accounts import Account


def test_execution_cost_deduct_from_account(one_node_network):
    """
    Feature file: execution_cost.feature
    Scenario: Cost of execution is deducted from the account that pays for session contract
    """
    node0: DockerNode = one_node_network.docker_nodes[0]
    node0.use_docker_client()

    def account_state(_block_hash):
        return node0.d_client.query_state(
            block_hash=_block_hash, key_type="address", key=node0.from_address, path=""
        )

    account1 = Account(1)
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    assert len(blocks) == 1  # There should be only one block, the genesis block
    block_hash_acct1 = node0.transfer_to_account(
        to_account_id=1, amount=10, from_account_id="genesis"
    )
    assert (
        node0.client.get_balance(
            account_address=account1.public_key_hex, block_hash=block_hash_acct1
        )
        == 10
    )
    response = account_state(block_hash_acct1)
    assert response.account.nonce == 1
    block_hash = node0.deploy_and_propose(
        from_address=account1.public_key_hex,
        session_contract="test_counterdefine.wasm",
        payment_contract="test_counterdefine.wasm",
        nonce=2,
    )
    assert block_hash is not None
    assert (
        node0.client.get_balance(
            account_address=account1.public_key_hex, block_hash=block_hash
        )
        == 10
    )
    # block1 = node0.client.show_block(block_hash)
    # block_ds = parse_show_block(block1)
