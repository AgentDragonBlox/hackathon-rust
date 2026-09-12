//! Direct port of agent_engines/fairness.py.

use std::collections::HashMap;
use std::collections::VecDeque;

pub struct RecencyTracker {
    window: usize,
    history: HashMap<String, VecDeque<u64>>,
    tick: u64,
}

impl RecencyTracker {
    pub fn new(window: usize) -> Self {
        Self {
            window,
            history: HashMap::new(),
            tick: 0,
        }
    }

    pub fn record(&mut self, asset_id: &str) {
        self.tick += 1;
        let deque = self
            .history
            .entry(asset_id.to_string())
            .or_insert_with(VecDeque::new);
        deque.push_back(self.tick);
        while deque.len() > self.window {
            deque.pop_front();
        }
    }

    pub fn count(&self, asset_id: &str, lookback: u64) -> u64 {
        if self.tick == 0 {
            return 0;
        }
        match self.history.get(asset_id) {
            None => 0,
            Some(deque) => deque
                .iter()
                .filter(|&&t| self.tick.saturating_sub(t) <= lookback)
                .count() as u64,
        }
    }
}

impl Default for RecencyTracker {
    fn default() -> Self {
        Self::new(20)
    }
}
