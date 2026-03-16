/*
 * barrier -- mouse and keyboard sharing utility
 *
 * This package is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * found in the file LICENSE that should have accompanied this file.
 *
 * This package is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "net/UDPSocket.h"
#include "base/Log.h"

#include <cstring>
#include <cerrno>

#ifdef _WIN32
#pragma comment(lib, "ws2_32.lib")
#endif

UDPSocket::UDPSocket() :
    m_fd(UDP_INVALID_SOCKET),
    m_hasTarget(false)
{
    std::memset(&m_target, 0, sizeof(m_target));
    createSocket();
    if (m_fd != UDP_INVALID_SOCKET) {
        setNonBlocking();
    }
}

UDPSocket::~UDPSocket()
{
    closeSocket();
}

void
UDPSocket::createSocket()
{
    m_fd = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (m_fd == UDP_INVALID_SOCKET) {
        LOG((CLOG_ERR "failed to create UDP socket: %s", std::strerror(errno)));
    }
}

void
UDPSocket::setNonBlocking()
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return;
    }

#ifdef _WIN32
    u_long mode = 1;
    if (ioctlsocket(m_fd, FIONBIO, &mode) != 0) {
        LOG((CLOG_ERR "failed to set UDP socket non-blocking"));
    }
#else
    int flags = fcntl(m_fd, F_GETFL, 0);
    if (flags == -1) {
        LOG((CLOG_ERR "failed to get UDP socket flags: %s", std::strerror(errno)));
        return;
    }
    if (fcntl(m_fd, F_SETFL, flags | O_NONBLOCK) == -1) {
        LOG((CLOG_ERR "failed to set UDP socket non-blocking: %s", std::strerror(errno)));
    }
#endif
}

void
UDPSocket::closeSocket()
{
    if (m_fd != UDP_INVALID_SOCKET) {
#ifdef _WIN32
        closesocket(m_fd);
#else
        ::close(m_fd);
#endif
        m_fd = UDP_INVALID_SOCKET;
    }
}

bool
UDPSocket::bind(UInt16 port)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return false;
    }

    // allow address reuse
    int opt = 1;
#ifdef _WIN32
    setsockopt(m_fd, SOL_SOCKET, SO_REUSEADDR,
               reinterpret_cast<const char*>(&opt), sizeof(opt));
#else
    setsockopt(m_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
#endif

    struct sockaddr_in addr;
    std::memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(port);

    if (::bind(m_fd, reinterpret_cast<struct sockaddr*>(&addr),
               sizeof(addr)) != 0) {
        LOG((CLOG_ERR "failed to bind UDP socket to port %d: %s",
             port, std::strerror(errno)));
        return false;
    }

    LOG((CLOG_DEBUG "UDP socket bound to port %d", port));
    return true;
}

bool
UDPSocket::setTarget(const char* host, UInt16 port)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return false;
    }

    std::memset(&m_target, 0, sizeof(m_target));
    m_target.sin_family = AF_INET;
    m_target.sin_port   = htons(port);

    // try as numeric address first
    if (inet_pton(AF_INET, host, &m_target.sin_addr) != 1) {
        LOG((CLOG_ERR "invalid UDP target address: %s", host));
        m_hasTarget = false;
        return false;
    }

    m_hasTarget = true;
    LOG((CLOG_DEBUG "UDP target set to %s:%d", host, port));
    return true;
}

int
UDPSocket::send(const void* data, int size)
{
    if (m_fd == UDP_INVALID_SOCKET || !m_hasTarget) {
        return -1;
    }

    return sendTo(data, size, m_target);
}

int
UDPSocket::sendTo(const void* data, int size,
                   const struct sockaddr_in& addr)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return -1;
    }

#ifdef _WIN32
    int n = ::sendto(m_fd, static_cast<const char*>(data), size, 0,
                     reinterpret_cast<const struct sockaddr*>(&addr),
                     sizeof(addr));
#else
    ssize_t n = ::sendto(m_fd, data, size, 0,
                         reinterpret_cast<const struct sockaddr*>(&addr),
                         sizeof(addr));
#endif

    if (n < 0) {
#ifdef _WIN32
        int err = WSAGetLastError();
        if (err != WSAEWOULDBLOCK) {
            LOG((CLOG_DEBUG2 "UDP sendto failed: error %d", err));
        }
#else
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
            LOG((CLOG_DEBUG2 "UDP sendto failed: %s", std::strerror(errno)));
        }
#endif
        return -1;
    }

    return static_cast<int>(n);
}

int
UDPSocket::recv(void* buffer, int maxSize, struct sockaddr_in* fromAddr)
{
    if (m_fd == UDP_INVALID_SOCKET) {
        return -1;
    }

    struct sockaddr_in sender;
    socklen_t senderLen = sizeof(sender);
    std::memset(&sender, 0, sizeof(sender));

#ifdef _WIN32
    int n = ::recvfrom(m_fd, static_cast<char*>(buffer), maxSize, 0,
                       reinterpret_cast<struct sockaddr*>(&sender),
                       &senderLen);
#else
    ssize_t n = ::recvfrom(m_fd, buffer, maxSize, 0,
                           reinterpret_cast<struct sockaddr*>(&sender),
                           &senderLen);
#endif

    if (n < 0) {
#ifdef _WIN32
        int err = WSAGetLastError();
        if (err == WSAEWOULDBLOCK) {
            return 0;
        }
        LOG((CLOG_DEBUG2 "UDP recvfrom failed: error %d", err));
#else
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return 0;
        }
        LOG((CLOG_DEBUG2 "UDP recvfrom failed: %s", std::strerror(errno)));
#endif
        return -1;
    }

    if (fromAddr != nullptr) {
        *fromAddr = sender;
    }

    return static_cast<int>(n);
}
