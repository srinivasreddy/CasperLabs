use common::key::Key;
use gs::{DbReader, TrackingCopy};
use shared::newtypes::Blake2bHash;
use std::collections::HashMap;
use transform::Transform;

// needs to be public for use in the gens crate
pub mod trie;

pub trait History {
    type Error;
    type Reader: DbReader;

    /// Checkouts to the post state of a specific block.
    fn checkout(
        &self,
        prestate_hash: Blake2bHash,
    ) -> Result<Option<TrackingCopy<Self::Reader>>, Self::Error>;

    /// Applies changes and returns a new post state hash.
    /// block_hash is used for computing a deterministic and unique keys.
    fn commit(
        &mut self,
        prestate_hash: Blake2bHash,
        effects: HashMap<Key, Transform>,
    ) -> Result<Option<Blake2bHash>, Self::Error>;
}
