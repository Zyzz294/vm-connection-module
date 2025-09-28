"""
Simple but robust VM connection module for test automation
"""

import logging
import time
import socket
from typing import Optional, Callable
import paramiko


# Simple logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Custom exceptions
class VMConnectionError(Exception):
    pass

class ConnectionTimeoutError(VMConnectionError):
    pass

class CommandTimeoutError(VMConnectionError):
    pass

class UnexpectedRebootError(VMConnectionError):
    pass

class ConnectionLostError(VMConnectionError):
    pass


class SSHConnection:
    """Simple, robust SSH connection for VM testing"""
    
    def __init__(self, host: str, user: str, key_path: str, port: int = 22):
        self.host = host
        self.user = user
        self.key_path = key_path
        self.port = port
        
        self.client = None
        self.connected = False
        self.boot_time = None
    
    def connect(self, timeout: int = 30):
        """Connect to VM"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            key = paramiko.RSAKey.from_private_key_file(self.key_path)
            self.client.connect(self.host, self.port, self.user, pkey=key, timeout=timeout)
            
            self.connected = True
            self._get_boot_time()
            logger.info(f"Connected to {self.host}")
            
        except socket.timeout:
            raise ConnectionTimeoutError(f"Connection timeout to {self.host}")
        except Exception as e:
            raise VMConnectionError(f"Connection failed: {e}")
    
    def _get_boot_time(self):
        """Get VM boot time for reboot detection"""
        try:
            _, stdout, _ = self.client.exec_command("stat -c %Y /proc/1")
            self.boot_time = stdout.read().decode().strip()
        except:
            self.boot_time = None
    
    def execute(self, command: str, timeout: int = 60, output_callback: Optional[Callable] = None) -> int:
        """Execute command with real-time output"""
        if not self.connected:
            raise ConnectionLostError("Not connected")
        
        # Check for reboot
        self._check_reboot()
        
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            
            # Stream output in real-time
            start_time = time.time()
            while True:
                # Check timeout
                if time.time() - start_time > timeout:
                    stdin.channel.close()
                    raise CommandTimeoutError(f"Command timeout: {command}")
                
                # Check if done
                if stdout.channel.exit_status_ready():
                    break
                
                # Read output
                if stdout.channel.recv_ready():
                    line = stdout.readline().rstrip()
                    if line and output_callback:
                        output_callback(line)
                
                if stderr.channel.recv_ready():
                    line = stderr.readline().rstrip()
                    if line and output_callback:
                        output_callback(f"STDERR: {line}")
                
                time.sleep(0.1)
            
            # Get remaining output
            for line in stdout:
                if output_callback:
                    output_callback(line.rstrip())
            for line in stderr:
                if output_callback:
                    output_callback(f"STDERR: {line.rstrip()}")
            
            return stdout.channel.recv_exit_status()
            
        except Exception as e:
            if "timeout" in str(e).lower():
                raise CommandTimeoutError(f"Command timeout: {command}")
            self.connected = False
            raise ConnectionLostError(f"Connection lost: {e}")
    
    def _check_reboot(self):
        """Check if VM rebooted"""
        if not self.boot_time:
            return
        
        try:
            _, stdout, _ = self.client.exec_command("stat -c %Y /proc/1")
            current_boot_time = stdout.read().decode().strip()
            
            if current_boot_time != self.boot_time:
                raise UnexpectedRebootError("VM rebooted unexpectedly")
        except UnexpectedRebootError:
            raise
        except:
            pass  # Can't check, continue
    
    def is_alive(self) -> bool:
        """Check if VM is responsive"""
        if not self.connected or not self.client:
            return False
        
        try:
            # Check SSH connection
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                self.connected = False
                return False
            
            # Test with simple command
            stdin, stdout, stderr = self.client.exec_command("echo test", timeout=5)
            
            # Wait for completion with timeout
            start_time = time.time()
            while not stdout.channel.exit_status_ready():
                if time.time() - start_time > 5:
                    return False
                time.sleep(0.1)
            
            return stdout.channel.recv_exit_status() == 0
            
        except:
            self.connected = False
            return False
    
    def reconnect(self, max_retries: int = 3, delay: int = 5) -> bool:
        """Try to reconnect"""
        self.disconnect()
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Reconnect attempt {attempt + 1}/{max_retries}")
                self.connect()
                if self.is_alive():
                    return True
            except:
                pass
            
            if attempt < max_retries - 1:
                time.sleep(delay)
        
        return False
    
    def disconnect(self):
        """Close connection"""
        if self.client:
            try:
                self.client.close()
            except:
                pass
            self.client = None
        self.connected = False
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()