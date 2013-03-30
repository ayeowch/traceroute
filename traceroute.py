#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
traceroute - Get traceroute results with associated geolocation information
for each hop for a specified host from geographically distant source(s).
"""

__author__ = 'Dazzlepod (info@dazzlepod.com)'
__copyright__ = 'Copyright (c) 2013 Dazzlepod'
__version__ = '$Revision: #18 $'

import datetime
import json
import optparse
import os
import re
import signal
import socket
import sys
import urllib
import urllib2
from multiprocessing import Process
from subprocess import Popen, PIPE


class Traceroute(object):
    """Traceroute instance."""
    def __init__(self, ip_address='8.8.8.8', country='US', tmp_dir='/tmp', no_geo=False, timeout=120, debug=False):
        super(Traceroute, self).__init__()

        self.ip_address = ip_address
        self.country = country
        self.tmp_dir = tmp_dir
        self.no_geo = no_geo
        self.timeout = timeout
        self.debug = debug

        # Traceroute servers from traceroute.org
        sources = {
            'US': {
                'url': 'http://www.net.princeton.edu/cgi-bin/traceroute.pl',
                'post_data': {'target': self.ip_address},
            },
            'CH': {
                'url': 'http://www.switch.ch/cgi-bin/network/nph-traceroute-opencms?destination=%s' % self.ip_address,
                'post_data': None,
            },
            'JP': {
                'url': 'http://www.harenet.ad.jp/cgi-bin/harenet/traceroute/traceroute.cgi',
                'post_data': {'host': self.ip_address},
            },
            # 'LO' for localhost in which case 'url' will be used as the command to execute traceroute locally
            'LO': {
                'url': 'traceroute %s' % self.ip_address,
                'post_data': None,
            },
            'BY': {
                'url': 'http://by104.activeby.net/lg/',
                'post_data': {'method': 'trace', 'host': self.ip_address, 'router': 'http://by104.activeby.net/lg/'}
            },
            'RU': {
                'url': 'http://ipnoc.zenon.net/pcgi/trace.pl?IP=%s' % self.ip_address,
                'post_data': None
            },
            'UK': {
                'url': 'http://ab.newnet.co.uk/cgi-bin/traceroute?%s' % self.ip_address,
                'post_data': None
            }
        }
        self.source = sources[self.country]

        # cache geocoded IP addresses during the lifetime of this instance
        self.locations = {}

    def traceroute(self):
        """Instead of running the actual traceroute command, we will fetch
        standard traceroute results from several publicly available webpages
        that are listed at traceroute.org. For each hop, we will then attach
        geolocation information to it."""
        self.print_debug("ip_address = %s" % self.ip_address)
        txt = os.path.join(self.tmp_dir, '%s.%s.txt' % (self.ip_address, self.country))
        if not os.path.exists(txt):
            if self.country == 'LO':
                (status_code, traceroute) = self.execute_cmd(self.source['url'])
            else:
                (status_code, traceroute) = self.get_traceroute_output()

            if status_code != 0 and status_code != 200:
                return {'error': status_code}

            f = open(txt, 'w')
            f.write(traceroute)
            f.close()

        traceroute = open(txt, 'r').read()

        # hops = dicts with keys: hop_num, hosts
        hops = self.get_hops(traceroute)

        # hops = dicts with keys: hop_num, hostname, ip_address, rtt
        hops = self.get_formatted_hops(hops)

        if not self.no_geo:
            # hops = dicts with keys: hop_num, hostname, ip_address, rtt, latitude, longitude
            hops = self.get_geocoded_hops(hops)

        return hops

    def get_traceroute_output(self):
        """Fetch traceroute output from a webpage."""
        url = self.source['url']
        (status_code, content) = self.urlopen(url, context = self.source['post_data'])

        content = content.strip()

        pattern = re.compile(r'<pre.*?>(?P<traceroute>.*?)</pre>', re.DOTALL|re.IGNORECASE)
        try:
            traceroute = re.findall(pattern, content)[0].strip()
        except IndexError:
            # Manually append closing </pre> for partially downloaded page
            content = '%s</pre>' % content
            traceroute = re.findall(pattern, content)[0].strip()

        return (status_code, traceroute)

    def get_hops(self, traceroute):
        """Get hops from a traceroute output and return the hops in an array
        of dicts each representing hop number and the associated hosts data."""
        hops = []

        lines = traceroute.split('\n')
        for line in lines:
            line = line.strip()
            hop = {}
            if not line: continue
            try:
                hop = re.match(r'^(?P<hop_num>\d+)(?P<hosts>.*?)$', line).groupdict()
            except AttributeError:
                continue
            self.print_debug(hop)
            hops.append(hop)

        return hops

    def get_formatted_hops(self, hops):
        """hosts data from get_hops() is represented in a single string.
        We use this function to better represent the hosts data in a dict."""
        formatted_hops = []

        for hop in hops:
            hop_num = int(hop['hop_num'].strip())
            hosts = hop['hosts'].replace('  ', ' ').strip()

            # Using re.findall(), first we split the hosts, then for each host we store a tuple containing hostname, IP address and the first round-trip time
            # [('<HOSTNAME>', '<IP_ADDRESS>', '<RTT1> ms'), ('<HOSTNAME_N>', '<IP_ADDRESS_N>', '<RTT1_N> ms')]
            hosts = re.findall(r'(?P<hostname>[\w.-]+) \((?P<ip_address>[\d.]+)\) (?P<rtt>\d{1,4}.\d{1,4} ms)', hosts)

            for host in hosts:
                hop_context = {
                    'hop_num': hop_num,
                    'hostname': host[0],
                    'ip_address': host[1],
                    'rtt': host[2],
                }
                self.print_debug(hop_context)
                formatted_hops.append(hop_context)

        return formatted_hops

    def get_geocoded_hops(self, hops):
        """Return hops from get_formatted_hops() with geolocation information
        for each hop."""
        geocoded_hops = []

        for hop in hops:
            ip_address = hop['ip_address']

            location = None
            if self.locations.has_key(ip_address):
                location = self.locations[ip_address]
            else:
                location = self.get_location(ip_address)
                self.locations[ip_address] = location

            if location:
                geocoded_hops.append({
                    'hop_num': hop['hop_num'],
                    'hostname': hop['hostname'],
                    'ip_address': hop['ip_address'],
                    'rtt': hop['rtt'],
                    'latitude': location['latitude'],
                    'longitude': location['longitude'],
                })

        return geocoded_hops

    def get_location(self, ip_address):
        """Return geolocation information for the specified IP address, e.g.:
            {"ip": "75.126.24.77",
            "hostname": "web365.webfaction.com",
            "isp": "SoftLayer Technologies",
            "organization": "Client Intellect",
            "country": "United States",
            "region": "Texas",
            "city": "Dallas",
            "latitude": 32.9299,
            "longitude": -96.8353}
        """
        location = None
        url = "https://dazzlepod.com/ip/%s.json" % ip_address
        (status_code, json_data) = self.urlopen(url)
        if status_code == 200 and json_data:
            tmp_location = json.loads(json_data)
            if tmp_location.has_key('latitude') and tmp_location.has_key('longitude'):
                location = tmp_location
        return location

    def execute_cmd(self, cmd):
        """Execute the specified command locally and return the resultant
        return code and output."""
        stdout = ''
        returncode = -1
        p = Popen(cmd, shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        try:
            signal.signal(signal.SIGALRM, self.signal_handler)
            signal.alarm(self.timeout)
            stdout, stderr = p.communicate()
            returncode = p.returncode
            self.print_debug("cmd = %s, returncode = %d" % (cmd, returncode))
            signal.alarm(0)
        except Exception, e:
            self.print_debug("%s" % str(e))
        return (returncode, stdout)

    def urlopen(self, url, context=None):
        """Perform HTTP GET/POST on the specified URL and return the resultant
        status code and response."""
        status_code = 200
        request = urllib2.Request(url = url)
        request.add_header('User-Agent', 'traceroute/1.0 (+https://github.com/ayeowch/traceroute')

        if context:
            data = urllib.urlencode(context)
            request.add_data(data)

        content = ''
        try:
            response = urllib2.urlopen(request)
            self.print_debug("url = %s\nheader = %s" % (response.geturl(), response.info()))
            content = self.chunked_read(response)
        except urllib2.HTTPError, e:
            status_code = e.code
        except urllib2.URLError:
            pass

        try:
            self.urlopen_count += 1
        except AttributeError:
            self.urlopen_count = 1
        self.print_debug("[%d] url = %s, status_code = %d" % (self.urlopen_count, url, status_code))

        return (status_code, content)

    def chunked_read(self, response):
        """Read page response in chunks. A signal handler is attached to abort
        reading after the set timeout.
        Chunk size = 64 bytes, max. page size = 1MB
        """
        content = ''
        max_bytes = 1 * 1024 * 1024
        completed_bytes = 0
        bytes_per_read = 64

        try:
            signal.signal(signal.SIGALRM, self.signal_handler)
            signal.alarm(self.timeout)
            while completed_bytes <= max_bytes:
                data = response.read(bytes_per_read)
                if not data:
                    break
                content += data
                completed_bytes += bytes_per_read
                self.print_debug("completed_bytes = %d, %s" % (completed_bytes, data))
            signal.alarm(0)
        except Exception, e:
            self.print_debug("%s" % str(e))

        return content

    def signal_handler(self, signum, frame):
        """Signal handler that simply raises an exception when triggered."""
        raise Exception("Caught signal %d" % signum)

    def print_debug(self, msg):
        """Print debug message to standard output."""
        if self.debug:
            print "[DEBUG %s] %s" % (datetime.datetime.now(), msg)


def main():
    usage = """%prog --ip_address=IP_ADDRESS"""
    cmdparser = optparse.OptionParser(usage, version=("traceroute " + __version__))
    cmdparser.add_option("-i", "--ip_address", type="string", default="8.8.8.8", help="IP address of destination host (default: 8.8.8.8)")
    cmdparser.add_option("-c", "--country", type="choice",
        choices=['US', 'CH', 'JP', 'LO', 'BY', 'RU', 'UK'],
        default="US",
        help="Traceroute will be initiated from this country; choose 'US' for United States, 'CH' for Switzerland, 'JP' for Japan or 'LO' for localhost to run traceroute locally (default: US)")
    cmdparser.add_option("-t", "--tmp_dir", type="string", default="/tmp", help="Temporary directory to store downloaded traceroute results (default: /tmp)")
    cmdparser.add_option("-n", "--no_geo", action="store_true", default=False, help="No geolocation data (default: False)")
    cmdparser.add_option("-s", "--timeout", type="int", default=120, help="Timeout in seconds for all downloads (default: 120)")
    cmdparser.add_option("-d", "--debug", action="store_true", default=False, help="Show debug output (default: False)")

    (options, args) = cmdparser.parse_args()

    if options.ip_address:
        traceroute = Traceroute(ip_address=options.ip_address,
            country=options.country,
            tmp_dir=options.tmp_dir,
            no_geo=options.no_geo,
            timeout=options.timeout,
            debug=options.debug)
        hops = traceroute.traceroute()
        hops = json.dumps(hops, indent=4)
        print hops
    else:
        cmdparser.print_usage()

    return 0


if __name__ == '__main__':
    sys.exit(main())
