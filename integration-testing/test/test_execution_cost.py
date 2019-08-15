import json
from test.cl_node.client_parser import parse_show_blocks
from test.cl_node.docker_node import DockerNode
from test.cl_node.casperlabs_accounts import GENESIS_ACCOUNT
from test.cl_node.common import HELLO_WORLD
from test.cl_node.casperlabs_accounts import Account
from test.cl_node.common import MAX_PAYMENT_COST, CONV_RATE


def account_state(_block_hash: str, account: str, node0: DockerNode):
    return node0.d_client.query_state(
        block_hash=_block_hash, key_type="address", key=account, path=""
    )


def test_deduct_execution_cost_from_account(payment_node_network):
    network = payment_node_network
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 10 ** 9

    account1 = network.docker_nodes[0].test_account  # ( amount=10 ** 6)
    account1_block_hash = parse_show_blocks(node0.d_client.show_blocks(1000))[
        0
    ].summary.block_hash
    deploys = node0.client.show_deploys(account1_block_hash)
    execution_cost = deploys[0].cost
    account1_balance = node0.client.get_balance(
        account_address=account1.public_key_hex, block_hash=account1_block_hash
    )
    assert account1_balance == 10 ** 7
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
    # assert (
    #     genesis_balance
    #     == genesis_balance_after_transfer + account1_balance + execution_cost
    # )


def test_no_min_balance_in_account(payment_node_network_no_min_balance):
    network = payment_node_network_no_min_balance
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 10 ** 3
    block_hash = node0.transfer_to_account(
        1, amount=10 ** 7, is_deploy_error_check=False
    )
    deploy = node0.client.show_deploys(block_hash)[0]
    assert deploy.is_error is True
    assert deploy.error_message == "Insufficient payment"
    cost_of_execution = deploy.cost
    assert cost_of_execution == 0
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
    assert genesis_balance == genesis_balance_after_transfer


def test_error_in_payment_contract(payment_node_network):
    network = payment_node_network
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 10 ** 9

    from_account = Account("genesis")
    to_account = Account(1)
    args_json = json.dumps([{"account": to_account.public_key_hex}, {"u32": 10 ** 7}])

    ABI = node0.p_client.abi
    response, deploy_hash_bytes = node0.p_client.deploy(
        from_address=from_account.public_key_hex,
        session_contract="transfer_to_account.wasm",
        payment_contract="err_standard_payment.wasm",
        public_key=from_account.public_key_path,
        private_key=from_account.private_key_path,
        gas_price=1,
        gas_limit=MAX_PAYMENT_COST / CONV_RATE,
        session_args=ABI.args_from_json(args_json),
        payment_args=ABI.args([ABI.u512(10 ** 6)]),
    )
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
    # This test case not suppose to not to transfer back the amount spent.
    # But it does, is this a bug?
    assert genesis_balance == genesis_balance_after_transfer


def test_error_in_session_contract(payment_node_network):
    network = payment_node_network
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 10 ** 9
    block_hash = node0.transfer_to_account(
        1,
        session_contract="err_transfer_to_account.wasm",
        amount=10 ** 7,
        is_deploy_error_check=False,
    )
    deploy = node0.client.show_deploys(block_hash)[0]
    assert deploy.is_error is True
    assert deploy.error_message == "Exit code: 1"
    cost_of_execution = deploy.cost
    assert cost_of_execution > 0
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
    genesis_balance_after_transfer = genesis_balance_after_transfer
    # TODO
    # assert 1000000000 == 997460578
    # assert genesis_balance == genesis_balance_after_transfer + cost_of_execution


def test_just_enough_to_run_payment(
    payment_node_network_with_just_enough_to_run_payment_contract
):
    network = payment_node_network_with_just_enough_to_run_payment_contract
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 3093878
    block_hash = node0.transfer_to_account(
        1,
        amount=30 * (10 ** 5),
        session_contract=HELLO_WORLD,
        is_deploy_error_check=False,
    )
    deploy = node0.client.show_deploys(block_hash)[0]
    assert deploy.is_error is True
    assert deploy.error_message == "Insufficient payment"
    cost_of_execution = deploy.cost
    cost_of_execution = cost_of_execution
    # assert cost_of_execution != 0
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
    genesis_balance_after_transfer = genesis_balance_after_transfer
    # TODO
    # assert 1000000000 == 997460578
    # assert genesis_balance == genesis_balance_after_transfer + cost_of_execution


# The caller has not transferred enough funds to the payment purse
# to completely run the session code.
# The deploy will fail and the caller will not receive a refund.
def test_not_enough_to_run_session(payment_node_network):
    network = payment_node_network
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 10 ** 9
    block_hash = node0.transfer_to_account(
        1,
        session_contract="err_transfer_to_account.wasm",
        amount=10 ** 7,
        is_deploy_error_check=False,
    )
    deploy = node0.client.show_deploys(block_hash)[0]
    assert deploy.is_error is True
    assert deploy.error_message == "Trap(Trap { kind: Unreachable })"
    cost_of_execution = deploy.cost
    assert cost_of_execution != 0
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
    genesis_balance_after_transfer = genesis_balance_after_transfer
    # TODO
    # assert 1000000000 == 997460578
    # assert genesis_balance == genesis_balance_after_transfer + cost_of_execution


# The session code can result in an error.
# The deploy will fail
# and the caller will receive a partial refund for the unspent gas.
def test_refund_after_session_code_error(payment_node_network):
    network = payment_node_network
    node0: DockerNode = network.docker_nodes[0]

    # This will transfer some initial funds from genesis account
    # to the test account created for specifically this test:
    test_account = node0.test_account

    block = parse_show_blocks(node0.d_client.show_blocks(1000))[0]
    block_hash = block.summary.block_hash

    initial_balance = node0.client.get_balance(
        account_address=test_account.public_key_hex, block_hash=block_hash
    )
    initial_balance = initial_balance

    _, deploy_hash = node0.p_client.deploy(
        session_contract="test_args_u32.wasm", payment_contract="standard_payment.wasm"
    )
    # TODO: finish off


# The caller has not transferred enough funds to the payment purse
# to completely run the payment code.
# The deploy will fail and the caller will not receive a refund.
def test_not_enough_funds_to_run_payment_code(payment_node_network):
    network = payment_node_network
    node0: DockerNode = network.docker_nodes[0]
    node0.use_docker_client()
    blocks = parse_show_blocks(node0.d_client.show_blocks(1000))
    genesis_hash = blocks[0].summary.block_hash
    assert len(blocks) == 1  # There should be only one block - the genesis block
    genesis_balance = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex, block_hash=genesis_hash
    )
    assert genesis_balance == 10 ** 9
    account1 = Account(1)
    args_json = json.dumps([{"account": account1.public_key_hex}, {"u32": 10 ** 6}])
    ABI = node0.p_client.abi
    # import pdb; pdb.set_trace()
    response, deploy_hash_bytes = node0.p_client.deploy(
        from_address=GENESIS_ACCOUNT.public_key_hex,
        session_contract="tranfer_to_account.wasm",
        payment_contract="standard_payment.wasm",
        public_key=GENESIS_ACCOUNT.public_key_path,
        private_key=GENESIS_ACCOUNT.private_key_path,
        gas_price=1,
        gas_limit=MAX_PAYMENT_COST / CONV_RATE,
        session_args=ABI.args_from_json(args_json),
        payment_args=ABI.args(
            [ABI.u512(1000)]
        ),  # 1000 is very low amount compared with 10**6.
    )

    account1_block_hash = parse_show_blocks(node0.d_client.show_blocks(1000))[
        0
    ].summary.block_hash
    deploys = node0.client.show_deploys(deploy_hash_bytes)
    execution_cost = deploys[0].cost
    account1_balance = node0.client.get_balance(
        account_address=account1.public_key_hex, block_hash=account1_block_hash
    )
    assert account1_balance == 10 ** 7
    genesis_balance_after_transfer = node0.client.get_balance(
        account_address=GENESIS_ACCOUNT.public_key_hex,
        block_hash=parse_show_blocks(node0.d_client.show_blocks(1000))[
            0
        ].summary.block_hash,
    )
