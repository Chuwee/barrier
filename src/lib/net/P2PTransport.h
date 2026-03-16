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

#pragma once

#include "base/EventTypes.h"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <sys/socket.h>
#include <netinet/in.h>
#endif

#include <string>
#include <vector>

class UDPSocket;

//! Abstract peer-to-peer transport interface
/*!
Provides direct WiFi peer-to-peer connectivity for low-latency
mouse datagrams, bypassing the router entirely. On macOS this is
implemented via AWDL (Apple Wireless Direct Link); future implementations
may use WiFi Direct on Linux/Windows.
*/
class P2PTransport {
public:
    virtual ~P2PTransport() {}

    //! Discovered peer info
    struct Peer {
        std::string name;
        struct sockaddr_in6 addr;  // link-local IPv6 on P2P interface
    };

    //! Start advertising and browsing for peers
    /*!
    \p serviceName is this device's name (used for matching peers).
    \p port is the UDP port for P2P datagrams.
    Returns true on success.
    */
    virtual bool start(const std::string& serviceName, UInt16 port) = 0;

    //! Stop advertising and browsing
    virtual void stop() = 0;

    //! Get discovered peers
    virtual std::vector<Peer> getPeers() const = 0;

    //! Find a specific peer by name (avoids copying the full vector)
    /*!
    Returns true if a peer with the given name exists, and copies
    its address into \p outAddr. Preferred on hot paths.
    */
    virtual bool findPeer(const std::string& name,
                          struct sockaddr_in6& outAddr) const = 0;

    //! Get the P2P UDP socket for sendTo/recvFrom
    virtual UDPSocket* getSocket() = 0;

    //! Is the P2P link active?
    virtual bool isActive() const = 0;
};
