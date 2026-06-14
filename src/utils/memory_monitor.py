"""Memory monitoring utilities."""

import psutil
import gc
from typing import Optional, Callable
from ..utils.logger import get_logger


class MemoryMonitor:
    """Monitor memory usage and trigger alerts."""
    
    def __init__(self, threshold_mb: Optional[float] = None,
                 alert_callback: Optional[Callable[[float], None]] = None):
        """Initialize memory monitor.
        
        Args:
            threshold_mb: Memory threshold in MB for alerts
            alert_callback: Callback function when threshold exceeded
        """
        self.threshold_mb = threshold_mb
        self.alert_callback = alert_callback
        self.logger = get_logger(__name__)
        self.peak_memory_mb = 0.0
        self.process = psutil.Process()
    
    def get_current_memory_mb(self) -> float:
        """Get current memory usage in MB.
        
        Returns:
            Current memory usage in MB
        """
        memory_info = self.process.memory_info()
        memory_mb = memory_info.rss / (1024 * 1024)
        
        # Track peak
        if memory_mb > self.peak_memory_mb:
            self.peak_memory_mb = memory_mb
        
        return memory_mb
    
    def get_system_memory_info(self) -> dict:
        """Get system memory information.
        
        Returns:
            Dictionary with system memory stats
        """
        virtual_mem = psutil.virtual_memory()
        
        return {
            'total_mb': virtual_mem.total / (1024 * 1024),
            'available_mb': virtual_mem.available / (1024 * 1024),
            'used_mb': virtual_mem.used / (1024 * 1024),
            'percent_used': virtual_mem.percent,
            'process_memory_mb': self.get_current_memory_mb(),
            'peak_memory_mb': self.peak_memory_mb
        }
    
    def check_threshold(self) -> bool:
        """Check if memory usage exceeds threshold.
        
        Returns:
            True if threshold exceeded
        """
        if self.threshold_mb is None:
            return False
        
        current_mb = self.get_current_memory_mb()
        
        if current_mb > self.threshold_mb:
            self.logger.warning(
                f"Memory threshold exceeded: {current_mb:.1f} MB > {self.threshold_mb:.1f} MB"
            )
            
            if self.alert_callback:
                self.alert_callback(current_mb)
            
            return True
        
        return False
    
    def force_garbage_collection(self):
        """Force garbage collection to free memory."""
        before_mb = self.get_current_memory_mb()
        gc.collect()
        after_mb = self.get_current_memory_mb()
        freed_mb = before_mb - after_mb
        
        self.logger.info(
            f"Garbage collection: freed {freed_mb:.1f} MB "
            f"({before_mb:.1f} MB -> {after_mb:.1f} MB)"
        )
    
    def log_memory_usage(self, context: str = ""):
        """Log current memory usage.
        
        Args:
            context: Context description for log message
        """
        current_mb = self.get_current_memory_mb()
        sys_info = self.get_system_memory_info()
        
        message = f"Memory usage"
        if context:
            message += f" ({context})"
        message += f": {current_mb:.1f} MB (peak: {self.peak_memory_mb:.1f} MB, "
        message += f"system: {sys_info['percent_used']:.1f}% used)"
        
        self.logger.info(message)
    
    def get_memory_stats(self) -> dict:
        """Get detailed memory statistics.
        
        Returns:
            Dictionary with memory stats
        """
        current_mb = self.get_current_memory_mb()
        sys_info = self.get_system_memory_info()
        
        return {
            'current_mb': current_mb,
            'peak_mb': self.peak_memory_mb,
            'threshold_mb': self.threshold_mb,
            'threshold_exceeded': self.check_threshold() if self.threshold_mb else False,
            'system_total_mb': sys_info['total_mb'],
            'system_available_mb': sys_info['available_mb'],
            'system_percent_used': sys_info['percent_used']
        }


def format_bytes(bytes_value: int) -> str:
    """Format bytes as human-readable string.
    
    Args:
        bytes_value: Number of bytes
        
    Returns:
        Formatted string (e.g., "1.5 GB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.1f} PB"
