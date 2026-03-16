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

#if !defined(LINKCONFIG__H)

#define LINKCONFIG__H

#include <QString>

struct LinkConfig
{
    int srcStart;   // 0-100 percentage
    int srcEnd;     // 0-100 percentage
    int dstStart;   // 0-100 percentage
    int dstEnd;     // 0-100 percentage
    int monitorIndex; // -1 = whole screen (default), 0+ = specific destination monitor
    int srcMonitorIndex; // -1 = whole screen (default), 0+ = specific source monitor

    LinkConfig() :
        srcStart(0), srcEnd(100),
        dstStart(0), dstEnd(100),
        monitorIndex(-1),
        srcMonitorIndex(-1) {}

    bool isDefault() const {
        return srcStart == 0 && srcEnd == 100 &&
               dstStart == 0 && dstEnd == 100 &&
               monitorIndex < 0 &&
               srcMonitorIndex < 0;
    }
};

// Key for storing link configs: "screenName:direction"
inline QString linkConfigKey(const QString& screenName, const QString& direction)
{
    return screenName + ":" + direction;
}

#endif
