/*
 * barrier -- mouse and keyboard sharing utility
 * Copyright (C) 2016 Symless.
 *
 * This package is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * found in the file COPYING that should have accompanied this file.
 *
 * This package is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "barrier/IPlatformScreen.h"
#include "barrier/protocol_types.h"

bool
IPlatformScreen::fakeMediaKey(KeyID id)
{
    return false;
}

void
IPlatformScreen::getMonitors(std::vector<MonitorGeometry>& monitors) const
{
    // default: single monitor matching combined screen shape
    monitors.clear();
    MonitorGeometry mg;
    getShape(mg.m_x, mg.m_y, mg.m_w, mg.m_h);
    monitors.push_back(mg);
}
