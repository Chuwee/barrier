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

#include "net/P2PTransport.h"
#include "net/UDPSocket.h"

#if defined(__APPLE__)

#include <dns_sd.h>
#include <mutex>
#include <thread>
#include <atomic>
#include <vector>
#include <map>
#include <memory>

//! AWDL peer-to-peer transport for macOS
/*!
Uses Apple Wireless Direct Link (AWDL) for direct WiFi peer-to-peer
connectivity, bypassing the router entirely. Discovers peers using
dns_sd with kDNSServiceInterfaceIndexP2P and communicates over IPv6
link-local addresses on the awdl0 interface.
*/
class AWDLTransport : public P2PTransport {
public:
    AWDLTransport();
    ~AWDLTransport();

    // P2PTransport interface
    bool start(const std::string& serviceName, UInt16 port) override;
    void stop() override;
    std::vector<Peer> getPeers() const override;
    bool findPeer(const std::string& name,
                  struct sockaddr_in6& outAddr) const override;
    UDPSocket* getSocket() override;
    bool isActive() const override;

private:
    // dns_sd callback wrappers
    static void DNSSD_API registerCallback(
        DNSServiceRef sdRef,
        DNSServiceFlags flags,
        DNSServiceErrorType errorCode,
        const char* name,
        const char* regtype,
        const char* domain,
        void* context);

    static void DNSSD_API browseCallback(
        DNSServiceRef sdRef,
        DNSServiceFlags flags,
        uint32_t interfaceIndex,
        DNSServiceErrorType errorCode,
        const char* serviceName,
        const char* regtype,
        const char* replyDomain,
        void* context);

    static void DNSSD_API resolveCallback(
        DNSServiceRef sdRef,
        DNSServiceFlags flags,
        uint32_t interfaceIndex,
        DNSServiceErrorType errorCode,
        const char* fullname,
        const char* hosttarget,
        uint16_t port,
        uint16_t txtLen,
        const unsigned char* txtRecord,
        void* context);

    static void DNSSD_API addrInfoCallback(
        DNSServiceRef sdRef,
        DNSServiceFlags flags,
        uint32_t interfaceIndex,
        DNSServiceErrorType errorCode,
        const char* hostname,
        const struct sockaddr* address,
        uint32_t ttl,
        void* context);

    // event loop thread
    void eventLoop();

    // context for in-flight resolve/addrInfo operations
    struct ResolveContext {
        AWDLTransport* self;
        std::string    peerName;
        uint32_t       ifIndex;
        bool           done;      // set by callback when no more results
        bool           cancelled; // set when peer removed before completion
    };

    // a pending dns_sd operation tracked in the event loop
    struct PendingRef {
        DNSServiceRef                   ref;
        std::shared_ptr<ResolveContext> ctx;
    };

    // queue a new pending ref from a callback (called on event thread)
    void addPendingRef(DNSServiceRef ref,
                       std::shared_ptr<ResolveContext> ctx);

    // cancel all pending refs for a peer (called on peer removal)
    void cancelPendingForPeer(const std::string& peerName);

    // state
    std::string         m_serviceName;
    UInt16              m_port;

    // dns_sd refs (long-lived)
    DNSServiceRef       m_registerRef;
    DNSServiceRef       m_browseRef;

    // short-lived resolve/addrInfo refs processed by the event loop
    std::mutex          m_pendingMutex;
    std::vector<PendingRef> m_pendingRefs;

    // P2P UDP socket (IPv6 on awdl0)
    UDPSocket*          m_socket;

    // discovered peers — map for O(1) lookup on hot path
    mutable std::mutex  m_peerMutex;
    std::map<std::string, struct sockaddr_in6> m_peerAddrs;

    // event loop thread
    std::thread         m_thread;
    std::atomic<bool>   m_running;
};

#else // !__APPLE__

//! Stub P2P transport for non-macOS platforms
class AWDLTransport : public P2PTransport {
public:
    bool start(const std::string&, UInt16) override { return false; }
    void stop() override {}
    std::vector<Peer> getPeers() const override { return {}; }
    bool findPeer(const std::string&, struct sockaddr_in6&) const override { return false; }
    UDPSocket* getSocket() override { return nullptr; }
    bool isActive() const override { return false; }
};

#endif // __APPLE__
