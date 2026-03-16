/*
 * barrier -- mouse and keyboard sharing utility
 * Copyright (C) 2012-2016 Symless Ltd.
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

#include "server/ClientProxy1_7.h"

#include "server/Server.h"
#include "barrier/ProtocolUtil.h"
#include "barrier/protocol_types.h"
#include "io/IStream.h"
#include "mt/Lock.h"
#include "base/Log.h"

#include <cstring>

//
// ClientProxy1_7
//

ClientProxy1_7::ClientProxy1_7(const std::string& name, barrier::IStream* stream,
                               Server* server, IEventQueue* events) :
    ClientProxy1_6(name, stream, server, events),
    m_events(events)
{
    // do nothing
}

ClientProxy1_7::~ClientProxy1_7()
{
    // do nothing
}

void
ClientProxy1_7::getMonitors(std::vector<MonitorGeometry>& monitors) const
{
    bool empty;
    {
        Lock lock(&m_monitorsMutex);
        empty = m_monitors.empty();
        if (!empty) {
            monitors = m_monitors;
        }
    }
    if (empty) {
        // fallback to default (single combined rect)
        BaseClientProxy::getMonitors(monitors);
    }
}

bool
ClientProxy1_7::parseMessage(const UInt8* code)
{
    if (memcmp(code, kMsgDMonitorInfo, 4) == 0) {
        recvMonitorInfo();
        return true;
    }
    else {
        return ClientProxy1_6::parseMessage(code);
    }
}

void
ClientProxy1_7::recvMonitorInfo()
{
    // read monitor count
    SInt16 count;
    ProtocolUtil::readf(getStream(), kMsgDMonitorInfo + 4, &count);

    std::vector<MonitorGeometry> newMonitors;
    for (SInt16 i = 0; i < count; ++i) {
        SInt16 x, y, w, h;
        ProtocolUtil::readf(getStream(), "%2i%2i%2i%2i", &x, &y, &w, &h);

        MonitorGeometry mg;
        mg.m_x = x;
        mg.m_y = y;
        mg.m_w = w;
        mg.m_h = h;
        newMonitors.push_back(mg);

        LOG((CLOG_DEBUG1 "client monitor %d: %d,%d %dx%d",
            i, mg.m_x, mg.m_y, mg.m_w, mg.m_h));
    }

    // swap under lock for thread safety
    {
        Lock lock(&m_monitorsMutex);
        m_monitors.swap(newMonitors);
    }

    LOG((CLOG_DEBUG "received %d monitor geometries from \"%s\"",
        (int)count, getName().c_str()));
}
