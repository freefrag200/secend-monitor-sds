//! BGRA -> JPEG encoding.
//!
//! Uses the `jpeg-encoder` crate which is pure Rust (no native deps), so the
//! final binary is fully self-contained — critical for an offline-LAN tool that
//! may run on a machine with no C toolchain installed.
//!
//! Input is BGRA (the format our capture produces); we de-interleave to the
//! RGB planar layout `jpeg-encoder` expects.

use jpeg_encoder::{ColorType, Encoder};

/// Encode a BGRA buffer as JPEG.  Returns the JPEG byte stream.
pub fn encode_bgra(bgra: &[u8], width: u32, height: u32, quality: u8) -> Vec<u8> {
    let q = quality.clamp(10, 100);

    // Convert BGRA -> packed RGB (drop alpha).
    let pixel_count = (width as usize) * (height as usize);
    let mut rgb = vec![0u8; pixel_count * 3];
    for (i, px) in bgra.chunks_exact(4).enumerate() {
        rgb[i * 3] = px[2]; // R
        rgb[i * 3 + 1] = px[1]; // G
        rgb[i * 3 + 2] = px[0]; // B
    }

    let mut out = Vec::new();
    let encoder = Encoder::new(&mut out, q);
    match encoder.encode(&rgb, width as u16, height as u16, ColorType::Rgb) {
        Ok(()) => out,
        Err(e) => {
            tracing::error!("JPEG encode failed: {e}; returning empty frame");
            Vec::new()
        }
    }
}
