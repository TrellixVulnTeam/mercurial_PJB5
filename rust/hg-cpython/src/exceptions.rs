// ancestors.rs
//
// Copyright 2018 Georges Racinet <gracinet@anybox.fr>
//
// This software may be used and distributed according to the terms of the
// GNU General Public License version 2 or any later version.

//! Bindings for Rust errors
//!
//! [`GraphError`] exposes `hg::GraphError` as a subclass of `ValueError`
//! but some variants of `hg::GraphError` can be converted directly to other
//! existing Python exceptions if appropriate.
//!
//! [`GraphError`]: struct.GraphError.html
use cpython::exc::ValueError;
use cpython::{PyErr, Python};
use hg;

py_exception!(rustext, GraphError, ValueError);

impl GraphError {
    pub fn pynew(py: Python, inner: hg::GraphError) -> PyErr {
        match inner {
            hg::GraphError::ParentOutOfRange(r) => {
                GraphError::new(py, ("ParentOutOfRange", r))
            }
            hg::GraphError::WorkingDirectoryUnsupported => {
                match py
                    .import("mercurial.error")
                    .and_then(|m| m.get(py, "WdirUnsupported"))
                {
                    Err(e) => e,
                    Ok(cls) => PyErr::from_instance(py, cls),
                }
            }
        }
    }
}
