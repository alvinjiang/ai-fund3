import socket

import pytest


def test_network_guard_blocks_socket_creation():
    """The autouse _block_network fixture must prevent real socket creation."""

    with pytest.raises(RuntimeError, match="network access blocked"):
        socket.socket()
