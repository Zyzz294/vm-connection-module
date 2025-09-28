"""
Simple tests for vm_connection module
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from vm_connection import (
    SSHConnection,
    VMConnectionError,
    ConnectionTimeoutError,
    CommandTimeoutError,
    UnexpectedRebootError,
    ConnectionLostError
)


@pytest.fixture
def conn():
    return SSHConnection('test-host', 'user', '/path/to/key')


@pytest.fixture
def mock_ssh():
    with patch('vm_connection.paramiko.SSHClient') as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Mock transport
        mock_transport = Mock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport
        
        yield mock_client


def test_init(conn):
    """Test connection initialization"""
    assert conn.host == 'test-host'
    assert conn.user == 'user'
    assert not conn.connected


@patch('vm_connection.paramiko.RSAKey.from_private_key_file')
def test_connect_success(mock_key, conn, mock_ssh):
    """Test successful connection"""
    mock_key.return_value = Mock()
    
    # Mock boot time check
    mock_stdout = Mock()
    mock_stdout.read.return_value = b'1234567890'
    mock_ssh.exec_command.return_value = (None, mock_stdout, None)
    
    conn.connect()
    
    assert conn.connected
    assert conn.boot_time == '1234567890'


@patch('vm_connection.paramiko.RSAKey.from_private_key_file')
def test_connect_timeout(mock_key, conn, mock_ssh):
    """Test connection timeout"""
    mock_key.return_value = Mock()
    mock_ssh.connect.side_effect = Exception("timeout")
    
    with pytest.raises(VMConnectionError):
        conn.connect()


def test_execute_success(conn, mock_ssh):
    """Test successful command execution"""
    conn.client = mock_ssh
    conn.connected = True
    conn.boot_time = '1234567890'
    
    # Mock command execution
    mock_channel = Mock()
    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_exit_status.return_value = 0
    mock_channel.recv_ready.return_value = False
    
    mock_stdout = Mock()
    mock_stdout.channel = mock_channel
    mock_stdout.readline.return_value = ""
    mock_stdout.__iter__ = lambda self: iter([])
    
    mock_stderr = Mock()
    mock_stderr.channel = mock_channel
    mock_stderr.readline.return_value = ""
    mock_stderr.__iter__ = lambda self: iter([])
    
    mock_ssh.exec_command.side_effect = [
        # Boot time check
        (None, Mock(read=lambda: b'1234567890'), None),
        # Actual command
        (Mock(), mock_stdout, mock_stderr)
    ]
    
    exit_code = conn.execute('echo test')
    assert exit_code == 0


def test_execute_with_callback(conn, mock_ssh):
    """Test command with output callback"""
    conn.client = mock_ssh
    conn.connected = True
    conn.boot_time = '1234567890'
    
    mock_channel = Mock()
    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_exit_status.return_value = 0
    mock_channel.recv_ready.side_effect = [True, False, False]
    
    mock_stdout = Mock()
    mock_stdout.channel = mock_channel
    mock_stdout.readline.side_effect = ["test output", ""]
    mock_stdout.__iter__ = lambda self: iter([])
    
    mock_stderr = Mock()
    mock_stderr.channel = mock_channel
    mock_stderr.readline.return_value = ""
    mock_stderr.__iter__ = lambda self: iter([])
    
    mock_ssh.exec_command.side_effect = [
        (None, Mock(read=lambda: b'1234567890'), None),
        (Mock(), mock_stdout, mock_stderr)
    ]
    
    output_lines = []
    def callback(line):
        output_lines.append(line)
    
    conn.execute('echo test', output_callback=callback)
    assert 'test output' in output_lines


@patch('vm_connection.time.time')
def test_execute_timeout(mock_time, conn, mock_ssh):
    """Test command timeout"""
    conn.client = mock_ssh
    conn.connected = True
    conn.boot_time = '1234567890'
    
    mock_time.side_effect = [0, 0, 61]  # Timeout after 60s
    
    mock_channel = Mock()
    mock_channel.exit_status_ready.return_value = False
    mock_channel.recv_ready.return_value = False
    
    mock_stdout = Mock()
    mock_stdout.channel = mock_channel
    
    mock_stderr = Mock()
    mock_stderr.channel = mock_channel
    
    mock_ssh.exec_command.side_effect = [
        (None, Mock(read=lambda: b'1234567890'), None),
        (Mock(), mock_stdout, mock_stderr)
    ]
    
    with pytest.raises(CommandTimeoutError):
        conn.execute('sleep 100', timeout=60)


def test_reboot_detection(conn, mock_ssh):
    """Test reboot detection"""
    conn.client = mock_ssh
    conn.connected = True
    conn.boot_time = '1234567890'
    
    # Return different boot time
    mock_ssh.exec_command.return_value = (
        None, 
        Mock(read=lambda: b'9999999999'), 
        None
    )
    
    with pytest.raises(UnexpectedRebootError):
        conn.execute('echo test')


def test_is_alive_healthy(conn, mock_ssh):
    """Test is_alive with healthy VM"""
    conn.client = mock_ssh
    conn.connected = True
    
    mock_channel = Mock()
    mock_channel.exit_status_ready.side_effect = [False, True]
    mock_channel.recv_exit_status.return_value = 0
    
    mock_stdout = Mock()
    mock_stdout.channel = mock_channel
    
    mock_ssh.exec_command.return_value = (Mock(), mock_stdout, Mock())
    
    assert conn.is_alive()


def test_is_alive_unhealthy(conn, mock_ssh):
    """Test is_alive with unhealthy VM"""
    conn.client = mock_ssh
    conn.connected = True
    
    mock_transport = Mock()
    mock_transport.is_active.return_value = False
    mock_ssh.get_transport.return_value = mock_transport
    
    assert not conn.is_alive()
    assert not conn.connected


@patch('vm_connection.time.sleep')
def test_reconnect_success(mock_sleep, conn):
    """Test successful reconnection"""
    with patch.object(conn, 'connect') as mock_connect, \
         patch.object(conn, 'is_alive', return_value=True), \
         patch.object(conn, 'disconnect'):
        
        assert conn.reconnect(max_retries=2)
        mock_connect.assert_called_once()


@patch('vm_connection.time.sleep')
def test_reconnect_failure(mock_sleep, conn):
    """Test failed reconnection"""
    with patch.object(conn, 'connect', side_effect=Exception("Failed")), \
         patch.object(conn, 'disconnect'):
        
        assert not conn.reconnect(max_retries=2)


def test_context_manager(conn):
    """Test context manager usage"""
    with patch.object(conn, 'connect') as mock_connect, \
         patch.object(conn, 'disconnect') as mock_disconnect:
        
        with conn:
            pass
        
        mock_connect.assert_called_once()
        mock_disconnect.assert_called_once()


def test_disconnect(conn, mock_ssh):
    """Test disconnection"""
    conn.client = mock_ssh
    conn.connected = True
    
    conn.disconnect()
    
    mock_ssh.close.assert_called_once()
    assert not conn.connected
    assert conn.client is None