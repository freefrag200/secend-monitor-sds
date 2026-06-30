//! Simple thread-safe ring buffer that keeps the freshest captured frame and
//! serves it to any number of MJPEG / WebRTC consumers without back-pressure.
//!
//! This is the Rust counterpart of Python's `ZeroLatencyBuffer` +
//! `FrameBroadcaster.latest_raw_np`.  A bounded `crossbeam` channel per
//! subscriber is used so slow clients drop frames instead of stalling capture.

use std::sync::Arc;
use std::time::Duration;

use crossbeam_channel::{bounded, Receiver, Sender, TrySendError};
use parking_lot::RwLock;

/// A complete frame ready for streaming: BGRA pixel buffer + dimensions +
/// virtual screen coordinates of the monitor it came from (used by the cursor
/// overlay).  The bytes are always BGRA (blue first) regardless of capture
/// backend so the downstream JPEG / scaling code can stay backend agnostic.
#[derive(Clone)]
pub struct Frame {
    pub width: u32,
    pub height: u32,
    /// BGRA pixel data, tightly packed, `width*height*4` bytes long.
    pub bgra: Vec<u8>,
    /// Virtual-screen coordinates of the source monitor.
    pub left: i32,
    pub top: i32,
    /// Logical width/height of the monitor (may differ from width/height when a
    /// non-native target resolution was requested).
    pub mon_w: i32,
    pub mon_h: i32,
    /// Pre-encoded JPEG bytes (produced by the capture pipeline). MJPEG clients
    /// consume this directly; `None` until the first encode pass completes.
    pub jpeg: Option<Vec<u8>>,
}

/// Handle handed out to each MJPEG client. Dropping it auto-unsubscribes.
pub struct Subscription {
    pub id: u64,
    pub rx: Receiver<Arc<Frame>>,
}

struct Inner {
    latest: Option<Arc<Frame>>,
    subs: Vec<(u64, Sender<Arc<Frame>>, Receiver<Arc<Frame>>)>,
    next_id: u64,
}

/// Fan-out frame store.  Call [`FrameHub::put`] from the capture thread, and
/// [`FrameHub::subscribe`] from each HTTP MJPEG handler.
#[derive(Clone)]
pub struct FrameHub {
    inner: Arc<RwLock<Inner>>,
}

impl Default for FrameHub {
    fn default() -> Self {
        Self::new()
    }
}

impl FrameHub {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(Inner {
                latest: None,
                subs: Vec::new(),
                next_id: 1,
            })),
        }
    }

    /// Returns the most recent frame, if any.  Does not block.
    pub fn latest(&self) -> Option<Arc<Frame>> {
        self.inner.read().latest.clone()
    }

    /// Publish a new frame and fan it out to every subscriber.  Slow consumers
    /// silently drop the oldest queued frame (bounded queue of 2) so capture
    /// is never blocked.
    pub fn put(&self, frame: Frame) {
        let arc = Arc::new(frame);
        let mut g = self.inner.write();
        g.latest = Some(arc.clone());
        for (_id, tx, rx) in &mut g.subs {
            match tx.try_send(arc.clone()) {
                Ok(()) => {}
                Err(TrySendError::Full(_)) => {
                    // Drop the stale queued frame, then push the freshest one.
                    let _ = rx.try_recv();
                    let _ = tx.try_send(arc.clone());
                }
                Err(TrySendError::Disconnected(_)) => {}
            }
        }
    }

    /// Register a new MJPEG subscriber.  Returns a handle whose `rx` yields
    /// frames.  Caps the total number of concurrent subscribers to 3 (matches
    /// the Python behaviour: extra clients are evicted).
    pub fn subscribe(&self) -> Subscription {
        let mut g = self.inner.write();
        // Evict oldest subscribers beyond the 3-client cap.
        while g.subs.len() >= 3 {
            let (_id, _tx, _rx) = g.subs.remove(0);
        }
        let (tx, rx) = bounded(2);
        let id = g.next_id;
        g.next_id += 1;
        // Prime the new subscriber with the latest frame so it doesn't block
        // waiting for the next capture tick.
        if let Some(latest) = g.latest.clone() {
            let _ = tx.try_send(latest);
        }
        g.subs.push((id, tx, rx.clone()));
        Subscription { id, rx }
    }

    /// Unsubscribe by id.  Called when an MJPEG client disconnects.
    pub fn unsubscribe(&self, id: u64) {
        let mut g = self.inner.write();
        g.subs.retain(|(sid, _, _)| *sid != id);
    }

    pub fn subscriber_count(&self) -> usize {
        self.inner.read().subs.len()
    }
}

/// Convenience: block up to `timeout` for the next frame on a subscription.
pub fn recv_timeout(rx: &Receiver<Arc<Frame>>, timeout: Duration) -> Option<Arc<Frame>> {
    rx.recv_timeout(timeout).ok()
}
