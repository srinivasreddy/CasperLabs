use std::collections::BTreeMap;
use std::convert::{TryFrom, TryInto};

use execution_engine::engine::{Error as EngineError, ExecutionResult};
use execution_engine::execution::Error as ExecutionError;
use ipc;
use shared::newtypes::Blake2bHash;
use storage::error::{Error::*, RootNotFound};
use storage::{gs, history, op, transform};

/// Helper method for turning instances of Value into Transform::Write.
fn transform_write(v: common::value::Value) -> Result<transform::Transform, ParsingError> {
    Ok(transform::Transform::Write(v))
}

#[derive(Debug)]
pub struct ParsingError(pub String);

/// Smart constructor for parse errors
fn parse_error<T>(message: String) -> Result<T, ParsingError> {
    Err(ParsingError(message))
}

impl TryFrom<&super::ipc::Transform> for transform::Transform {
    type Error = ParsingError;
    fn try_from(tr: &super::ipc::Transform) -> Result<transform::Transform, ParsingError> {
        if tr.has_identity() {
            Ok(transform::Transform::Identity)
        } else if tr.has_add_keys() {
            let keys_map = tr
                .get_add_keys()
                .get_value()
                .iter()
                .map(|nk| {
                    let local_nk = nk.clone();
                    local_nk.get_key().try_into().map(|k| (local_nk.name, k))
                })
                .collect::<Result<BTreeMap<String, common::key::Key>, ParsingError>>()?;
            Ok(transform::Transform::AddKeys(keys_map))
        } else if tr.has_add_i32() {
            Ok(transform::Transform::AddInt32(tr.get_add_i32().value))
        } else if tr.has_write() {
            let v = tr.get_write().get_value();
            if v.has_integer() {
                transform_write(common::value::Value::Int32(v.get_integer()))
            } else if v.has_big_int() {
                let n = v.get_big_int();
                let n_digs = n.len();
                let maybe_v = if n_digs < 39 {
                    // 2^128 ~= 3.4 x 10^38
                    common::value::U128::from_dec_str(n).map(common::value::Value::from)
                } else if n_digs < 78 {
                    // 2^256 ~= 1.2 x 10^77
                    common::value::U256::from_dec_str(n).map(common::value::Value::from)
                } else {
                    common::value::U512::from_dec_str(n).map(common::value::Value::from)
                };
                let v = maybe_v.map_err(|e| ParsingError(format!("{:?}", e)))?;
                transform_write(v)
            } else if v.has_byte_arr() {
                let v: Vec<u8> = Vec::from(v.get_byte_arr());
                transform_write(common::value::Value::ByteArray(v))
            } else if v.has_int_list() {
                let list = v.get_int_list().list.clone();
                transform_write(common::value::Value::ListInt32(list))
            } else if v.has_string_val() {
                transform_write(common::value::Value::String(v.get_string_val().to_string()))
            } else if v.has_account() {
                let mut pub_key = [0u8; 32];
                let uref_map: URefMap = v.get_account().get_known_urefs().try_into()?;
                pub_key.clone_from_slice(&v.get_account().pub_key);
                let account =
                    common::value::Account::new(pub_key, v.get_account().nonce as u64, uref_map.0);
                transform_write(common::value::Value::Account(account))
            } else if v.has_contract() {
                let ipc_contr = v.get_contract();
                let contr_body = ipc_contr.get_body().to_vec();
                let known_urefs: URefMap = ipc_contr.get_known_urefs().try_into()?;
                transform_write(common::value::Contract::new(contr_body, known_urefs.0).into())
            } else if v.has_string_list() {
                let list = v.get_string_list().list.to_vec();
                transform_write(common::value::Value::ListString(list))
            } else if v.has_named_key() {
                let nk = v.get_named_key();
                let name = nk.get_name().to_string();
                let key = nk.get_key().try_into()?;
                transform_write(common::value::Value::NamedKey(name, key))
            } else {
                parse_error(format!(
                    "TransformEntry write contained unknown value: {:?}",
                    v
                ))
            }
        } else {
            parse_error("TransformEntry couldn't be parsed to known Transform.".to_owned())
        }
    }
}

impl From<common::value::Value> for super::ipc::Value {
    fn from(v: common::value::Value) -> Self {
        let mut tv = super::ipc::Value::new();
        match v {
            common::value::Value::Int32(i) => {
                tv.set_integer(i);
            }
            common::value::Value::UInt128(u) => tv.set_big_int(format!("{}", u)),
            common::value::Value::UInt256(u) => tv.set_big_int(format!("{}", u)),
            common::value::Value::UInt512(u) => tv.set_big_int(format!("{}", u)),
            common::value::Value::ByteArray(arr) => {
                tv.set_byte_arr(arr);
            }
            common::value::Value::ListInt32(list) => {
                let mut int_list = super::ipc::IntList::new();
                int_list.set_list(list);
                tv.set_int_list(int_list);
            }
            common::value::Value::String(string) => {
                tv.set_string_val(string);
            }
            common::value::Value::ListString(list_string) => {
                let mut string_list = super::ipc::StringList::new();
                string_list.set_list(protobuf::RepeatedField::from_ref(list_string));
                tv.set_string_list(string_list);
            }
            common::value::Value::NamedKey(name, key) => {
                let named_key = {
                    let mut nk = super::ipc::NamedKey::new();
                    nk.set_name(name.to_string());
                    nk.set_key((&key).into());
                    nk
                };
                tv.set_named_key(named_key);
            }
            common::value::Value::Account(account) => {
                let mut acc = super::ipc::Account::new();
                acc.set_pub_key(account.pub_key().to_vec());
                acc.set_nonce(account.nonce());
                let urefs = URefMap(account.get_urefs_lookup()).into();
                acc.set_known_urefs(protobuf::RepeatedField::from_vec(urefs));
                tv.set_account(acc);
            }
            common::value::Value::Contract(contract) => {
                let (bytes, known_urefs) = contract.destructure();
                let mut contr = super::ipc::Contract::new();
                let urefs = URefMap(known_urefs).into();
                contr.set_body(bytes);
                contr.set_known_urefs(protobuf::RepeatedField::from_vec(urefs));
                tv.set_contract(contr);
            }
        };
        tv
    }
}

impl From<transform::Transform> for super::ipc::Transform {
    fn from(tr: transform::Transform) -> Self {
        let mut t = super::ipc::Transform::new();
        match tr {
            transform::Transform::Identity => {
                t.set_identity(super::ipc::TransformIdentity::new());
            }
            transform::Transform::Write(v) => {
                let mut tw = super::ipc::TransformWrite::new();
                tw.set_value(v.into());
                t.set_write(tw)
            }
            transform::Transform::AddInt32(i) => {
                let mut add = super::ipc::TransformAddInt32::new();
                add.set_value(i);
                t.set_add_i32(add);
            }
            transform::Transform::AddKeys(keys_map) => {
                let mut add = super::ipc::TransformAddKeys::new();
                let keys = URefMap(keys_map).into();
                add.set_value(protobuf::RepeatedField::from_vec(keys));
                t.set_add_keys(add);
            }
            transform::Transform::Failure(transform::TypeMismatch { expected, found }) => {
                let mut fail = super::ipc::TransformFailure::new();
                let mut typemismatch_err = super::ipc::StorageTypeMismatch::new();
                typemismatch_err.set_expected(expected.to_owned());
                typemismatch_err.set_found(found.to_owned());
                fail.set_error(typemismatch_err);
                t.set_failure(fail);
            }
        };
        t
    }
}

// newtype because trait impl have to be defined in the crate of the type.
pub struct URefMap(BTreeMap<String, common::key::Key>);

// Helper method for turning gRPC Vec of NamedKey to domain BTreeMap.
impl TryFrom<&[super::ipc::NamedKey]> for URefMap {
    type Error = ParsingError;
    fn try_from(from: &[super::ipc::NamedKey]) -> Result<Self, ParsingError> {
        let mut tree: BTreeMap<String, common::key::Key> = BTreeMap::new();
        for nk in from {
            let name = nk.get_name().to_string();
            let key = nk.get_key().try_into()?;
            let _ = tree.insert(name, key);
        }
        Ok(URefMap(tree))
    }
}

impl From<URefMap> for Vec<super::ipc::NamedKey> {
    fn from(uref_map: URefMap) -> Vec<super::ipc::NamedKey> {
        uref_map
            .0
            .into_iter()
            .map(|(n, k)| {
                let mut nk = super::ipc::NamedKey::new();
                nk.set_name(n);
                nk.set_key((&k).into());
                nk
            })
            .collect()
    }
}

impl From<&common::key::Key> for super::ipc::Key {
    fn from(key: &common::key::Key) -> super::ipc::Key {
        let mut k = super::ipc::Key::new();
        match key {
            common::key::Key::Account(acc) => {
                let mut key_addr = super::ipc::KeyAddress::new();
                key_addr.set_account(acc.to_vec());
                k.set_account(key_addr);
            }
            common::key::Key::Hash(hash) => {
                let mut key_hash = super::ipc::KeyHash::new();
                key_hash.set_key(hash.to_vec());
                k.set_hash(key_hash);
            }
            common::key::Key::URef(uref) => {
                let mut key_uref = super::ipc::KeyURef::new();
                key_uref.set_uref(uref.to_vec());
                k.set_uref(key_uref);
            }
        }
        k
    }
}

impl TryFrom<&super::ipc::Key> for common::key::Key {
    type Error = ParsingError;

    fn try_from(ipc_key: &super::ipc::Key) -> Result<Self, ParsingError> {
        if ipc_key.has_account() {
            let mut arr = [0u8; 20];
            arr.clone_from_slice(&ipc_key.get_account().account);
            Ok(common::key::Key::Account(arr))
        } else if ipc_key.has_hash() {
            let mut arr = [0u8; 32];
            arr.clone_from_slice(&ipc_key.get_hash().key);
            Ok(common::key::Key::Hash(arr))
        } else if ipc_key.has_uref() {
            let mut arr = [0u8; 32];
            arr.clone_from_slice(&ipc_key.get_uref().uref);
            Ok(common::key::Key::URef(arr))
        } else {
            // TODO make this Result::Err instead of panic
            parse_error(format!(
                "ipc Key couldn't be parsed to any Key: {:?}",
                ipc_key
            ))
        }
    }
}

impl From<op::Op> for super::ipc::Op {
    fn from(op: op::Op) -> super::ipc::Op {
        let mut ipc_op = super::ipc::Op::new();
        match op {
            op::Op::Read => ipc_op.set_read(super::ipc::ReadOp::new()),
            op::Op::Write => ipc_op.set_write(super::ipc::WriteOp::new()),
            op::Op::Add => ipc_op.set_add(super::ipc::AddOp::new()),
            op::Op::NoOp => ipc_op.set_noop(super::ipc::NoOp::new()),
        };
        ipc_op
    }
}

/// Transforms gRPC TransformEntry into domain tuple of (Key, Transform).
impl TryFrom<&super::ipc::TransformEntry> for (common::key::Key, transform::Transform) {
    type Error = ParsingError;
    fn try_from(from: &super::ipc::TransformEntry) -> Result<Self, ParsingError> {
        if from.has_key() {
            if from.has_transform() {
                let t: transform::Transform = from.get_transform().try_into()?;
                let key = from.get_key().try_into()?;
                Ok((key, t))
            } else {
                parse_error("No transform field in TransformEntry".to_owned())
            }
        } else {
            parse_error("No key field in TransformEntry".to_owned())
        }
    }
}

impl From<gs::ExecutionEffect> for super::ipc::ExecutionEffect {
    fn from(ee: gs::ExecutionEffect) -> super::ipc::ExecutionEffect {
        let mut eff = super::ipc::ExecutionEffect::new();
        let ipc_ops: Vec<super::ipc::OpEntry> =
            ee.0.iter()
                .map(|(k, o)| {
                    let mut op_entry = super::ipc::OpEntry::new();
                    let ipc_key = k.into();
                    let ipc_op = o.clone().into();
                    op_entry.set_key(ipc_key);
                    op_entry.set_operation(ipc_op);
                    op_entry
                })
                .collect();
        let ipc_tran: Vec<super::ipc::TransformEntry> =
            ee.1.into_iter()
                .map(|(k, t)| {
                    let mut tr_entry = super::ipc::TransformEntry::new();

                    let ipc_tr = t.into();
                    tr_entry.set_key((&k).into());
                    tr_entry.set_transform(ipc_tr);
                    tr_entry
                })
                .collect();
        eff.set_op_map(protobuf::RepeatedField::from_vec(ipc_ops));
        eff.set_transform_map(protobuf::RepeatedField::from_vec(ipc_tran));
        eff
    }
}

impl From<RootNotFound> for ipc::RootNotFound {
    fn from(err: RootNotFound) -> ipc::RootNotFound {
        let RootNotFound(missing_root_hash) = err;
        let mut root_missing_err = ipc::RootNotFound::new();
        root_missing_err.set_hash(missing_root_hash.to_vec());
        root_missing_err
    }
}

impl From<ExecutionResult> for ipc::DeployResult {
    fn from(er: ExecutionResult) -> ipc::DeployResult {
        match er {
            ExecutionResult {
                result: Ok(effects),
                cost,
            } => {
                let mut ipc_ee = effects.into();
                let mut deploy_result = ipc::DeployResult::new();
                deploy_result.set_effects(ipc_ee);
                deploy_result.set_cost(cost);
                deploy_result
            }
            ExecutionResult {
                result: Err(err),
                cost,
            } => {
                match err {
                    // TODO(mateusz.gorski): Fix error model for the storage errors.
                    // We don't have separate IPC messages for storage errors
                    // so for the time being they are all reported as "wasm errors".
                    EngineError::StorageError(storage_err) => {
                        let mut err = match storage_err {
                            KeyNotFound(key) => {
                                let msg = format!("Key {:?} not found.", key);
                                wasm_error(msg)
                            }
                            RkvError(error_msg) => wasm_error(error_msg),
                            TransformTypeMismatch(transform::TypeMismatch { expected, found }) => {
                                let msg = format!(
                                    "Type mismatch. Expected {:?}, found {:?}",
                                    expected, found
                                );
                                wasm_error(msg)
                            }
                            BytesRepr(bytesrepr_err) => {
                                let msg =
                                    format!("Error with byte representation: {:?}", bytesrepr_err);
                                wasm_error(msg)
                            }
                        };
                        err.set_cost(cost);
                        err
                    }
                    EngineError::PreprocessingError(err_msg) => {
                        let mut err = wasm_error(err_msg);
                        err.set_cost(cost);
                        err
                    }
                    EngineError::ExecError(exec_error) => match exec_error {
                        ExecutionError::GasLimit => {
                            let mut deploy_result = ipc::DeployResult::new();
                            let mut deploy_error = ipc::DeployError::new();
                            deploy_error.set_gasErr(ipc::OutOfGasError::new());
                            deploy_result.set_error(deploy_error);
                            deploy_result.set_cost(cost);
                            deploy_result
                        }
                        // TODO(mateusz.gorski): Be more specific about execution errors
                        other => {
                            let msg = format!("{:?}", other);
                            let mut err = wasm_error(msg);
                            err.set_cost(cost);
                            err
                        }
                    },
                }
            }
        }
    }
}

pub fn grpc_response_from_commit_result<R, H>(
    prestate_hash: Blake2bHash,
    input: Result<Option<Blake2bHash>, H::Error>,
) -> ipc::CommitResponse
where
    R: gs::DbReader,
    H: history::History<R>,
    H::Error: Into<EngineError> + std::fmt::Debug,
{
    match input {
        Ok(None) => {
            let mut root = ipc::RootNotFound::new();
            root.set_hash(prestate_hash.to_vec());
            let mut tmp_res = ipc::CommitResponse::new();
            tmp_res.set_missing_prestate(root);
            tmp_res
        }
        Ok(Some(post_state_hash)) => {
            println!("Effects applied. New state hash is: {:?}", post_state_hash);
            let mut commit_result = ipc::CommitResult::new();
            let mut tmp_res = ipc::CommitResponse::new();
            commit_result.set_poststate_hash(post_state_hash.to_vec());
            tmp_res.set_success(commit_result);
            tmp_res
        }
        // TODO(mateusz.gorski): We should be more specific about errors here.
        Err(storage_error) => {
            println!("Error {:?} when applying effects", storage_error);
            let mut err = ipc::PostEffectsError::new();
            let mut tmp_res = ipc::CommitResponse::new();
            err.set_message(format!("{:?}", storage_error));
            tmp_res.set_failed_transform(err);
            tmp_res
        }
    }
}

fn wasm_error(msg: String) -> ipc::DeployResult {
    let mut deploy_result = ipc::DeployResult::new();
    let mut deploy_error = ipc::DeployError::new();
    let mut err = ipc::WasmError::new();
    err.set_message(msg.to_owned());
    deploy_error.set_wasmErr(err);
    deploy_result.set_error(deploy_error);
    deploy_result
}

#[cfg(test)]
mod tests {
    use super::wasm_error;
    use common::key::Key;
    use execution_engine::engine::{Error as EngineError, ExecutionResult};
    use shared::newtypes::Blake2bHash;
    use std::collections::HashMap;
    use std::convert::TryInto;
    use storage::gs::ExecutionEffect;
    use storage::transform::Transform;

    // Test that wasm_error function actually returns DeployResult with result set to WasmError
    #[test]
    fn wasm_error_result() {
        let error_msg = "WasmError";
        let mut result = wasm_error(error_msg.to_owned());
        assert!(result.has_error());
        let mut ipc_error = result.take_error();
        assert!(ipc_error.has_wasmErr());
        let ipc_wasm_error = ipc_error.take_wasmErr();
        let ipc_error_msg = ipc_wasm_error.get_message();
        assert_eq!(ipc_error_msg, error_msg);
    }

    #[test]
    fn deploy_result_to_ipc_missing_root() {
        let root_hash: Blake2bHash = [1u8; 32].into();
        let mut result: super::ipc::RootNotFound = storage::error::RootNotFound(root_hash).into();
        let ipc_missing_hash = result.take_hash();
        assert_eq!(root_hash.to_vec(), ipc_missing_hash);
    }

    #[test]
    fn deploy_result_to_ipc_success() {
        let input_transforms: HashMap<Key, Transform> = {
            let mut tmp_map = HashMap::new();
            tmp_map.insert(Key::Account([1u8; 20]), Transform::AddInt32(10));
            tmp_map
        };
        let execution_effect: ExecutionEffect =
            ExecutionEffect(HashMap::new(), input_transforms.clone());
        let cost: u64 = 123;
        let execution_result: ExecutionResult = ExecutionResult::success(execution_effect, cost);
        let mut ipc_deploy_result: super::ipc::DeployResult = execution_result.into();
        assert_eq!(ipc_deploy_result.get_cost(), cost);

        // Extract transform map from the IPC message and parse it back to the domain
        let ipc_transforms: HashMap<Key, Transform> = {
            let mut ipc_effects = ipc_deploy_result.take_effects();
            let ipc_effects_tnfs = ipc_effects.take_transform_map().into_vec();
            ipc_effects_tnfs
                .iter()
                .map(|e| e.try_into())
                .collect::<Result<HashMap<Key, Transform>, _>>()
                .unwrap()
        };
        assert_eq!(&input_transforms, &ipc_transforms);
    }

    fn into_execution_failure<E: Into<EngineError>>(error: E, cost: u64) -> ExecutionResult {
        ExecutionResult::failure(error.into(), cost)
    }

    fn test_cost<E: Into<EngineError>>(expected_cost: u64, err: E) -> u64 {
        let execution_failure = into_execution_failure(err, expected_cost);
        let ipc_deploy_result: super::ipc::DeployResult = execution_failure.into();
        ipc_deploy_result.get_cost()
    }

    #[test]
    fn storage_error_has_cost() {
        use storage::error::Error::*;
        let cost: u64 = 100;
        assert_eq!(test_cost(cost, KeyNotFound(Key::Account([1u8; 20]))), cost);
        assert_eq!(test_cost(cost, RkvError("Error".to_owned())), cost);
        let type_mismatch = storage::transform::TypeMismatch {
            expected: "expected".to_owned(),
            found: "found".to_owned(),
        };
        assert_eq!(test_cost(cost, TransformTypeMismatch(type_mismatch)), cost);
        let bytesrepr_err = common::bytesrepr::Error::EarlyEndOfStream;
        assert_eq!(test_cost(cost, BytesRepr(bytesrepr_err)), cost);
    }

    #[test]
    fn preprocessing_err_has_cost() {
        let cost: u64 = 100;
        // it doesn't matter what error type it is
        let preprocessing_error = wasm_prep::PreprocessingError::NoExportSection;
        assert_eq!(test_cost(cost, preprocessing_error), cost);
    }

    #[test]
    fn exec_err_has_cost() {
        let cost: u64 = 100;
        // GasLimit error is treated differently at the moment so test separately
        assert_eq!(
            test_cost(cost, execution_engine::execution::Error::GasLimit),
            cost
        );
        // for the time being all other execution errors are treated in the same way
        let forged_ref_error =
            execution_engine::execution::Error::ForgedReference(Key::Account([1u8; 20]));
        assert_eq!(test_cost(cost, forged_ref_error), cost);
    }
}
