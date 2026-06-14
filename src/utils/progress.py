"""Progress tracking utilities for long-running operations."""

import time
import sys
from typing import Optional
from ..utils.logger import get_logger


class ProgressTracker:
    """Track and display progress for long-running operations."""
    
    def __init__(self, total: int, description: str = "Processing",
                 show_bar: bool = True, bar_width: int = 40):
        """Initialize progress tracker.
        
        Args:
            total: Total number of items to process
            description: Description of operation
            show_bar: Whether to show progress bar
            bar_width: Width of progress bar in characters
        """
        self.total = total
        self.description = description
        self.show_bar = show_bar
        self.bar_width = bar_width
        self.current = 0
        self.start_time = time.time()
        self.logger = get_logger(__name__)
        
    def update(self, current: int):
        """Update progress.
        
        Args:
            current: Current number of items processed
        """
        self.current = current
        
        if self.show_bar:
            self._display_progress_bar()
    
    def increment(self, amount: int = 1):
        """Increment progress by amount.
        
        Args:
            amount: Amount to increment by
        """
        self.update(self.current + amount)
    
    def _display_progress_bar(self):
        """Display progress bar to console."""
        percent = (self.current / self.total) * 100 if self.total > 0 else 0
        elapsed = time.time() - self.start_time
        rate = self.current / elapsed if elapsed > 0 else 0
        remaining = (self.total - self.current) / rate if rate > 0 else 0
        
        # Create progress bar
        filled = int(self.bar_width * self.current / self.total) if self.total > 0 else 0
        bar = '█' * filled + '░' * (self.bar_width - filled)
        
        # Format output
        output = (
            f"\r{self.description}: |{bar}| "
            f"{percent:5.1f}% "
            f"({self.current:,}/{self.total:,}) "
            f"[{rate:,.0f} rows/s, ETA: {self._format_time(remaining)}]"
        )
        
        sys.stdout.write(output)
        sys.stdout.flush()
        
        # New line when complete
        if self.current >= self.total:
            sys.stdout.write('\n')
            sys.stdout.flush()
    
    def _format_time(self, seconds: float) -> str:
        """Format seconds as human-readable time.
        
        Args:
            seconds: Number of seconds
            
        Returns:
            Formatted time string
        """
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds/60:.0f}m"
        else:
            return f"{seconds/3600:.1f}h"
    
    def finish(self):
        """Mark progress as complete."""
        self.update(self.total)
        elapsed = time.time() - self.start_time
        self.logger.info(
            f"{self.description} complete: {self.total:,} items in "
            f"{self._format_time(elapsed)} ({self.total/elapsed:.0f} items/s)"
        )
    
    def get_stats(self) -> dict:
        """Get progress statistics.
        
        Returns:
            Dictionary with progress stats
        """
        elapsed = time.time() - self.start_time
        rate = self.current / elapsed if elapsed > 0 else 0
        remaining = (self.total - self.current) / rate if rate > 0 else 0
        
        return {
            'current': self.current,
            'total': self.total,
            'percent': (self.current / self.total) * 100 if self.total > 0 else 0,
            'elapsed_seconds': elapsed,
            'rate_per_second': rate,
            'eta_seconds': remaining,
            'is_complete': self.current >= self.total
        }


class SimpleProgressCallback:
    """Simple callback for progress updates."""
    
    def __init__(self, description: str = "Processing"):
        """Initialize callback.
        
        Args:
            description: Operation description
        """
        self.description = description
        self.tracker: Optional[ProgressTracker] = None
    
    def __call__(self, current: int, total: int):
        """Progress callback.
        
        Args:
            current: Current progress
            total: Total items
        """
        if self.tracker is None:
            self.tracker = ProgressTracker(total, self.description)
        
        self.tracker.update(current)
