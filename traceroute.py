#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Multi-source traceroute with geolocation information.
"""

import datetime
import json
import optparse
import os
import re
import signal
import sys
import urllib
import urllib2
from subprocess import Popen, PIPE

USER_AGENT = "traceroute/1.0 (+https://github.com/ayeowch/traceroute)"


class Traceroute(object):
    """
    Multi-source traceroute instance.
    """
    def __init__(self, ip_address, source=None, country="US", tmp_dir="/tmp",
                 no_geo=False, timeout=120, debug=False):
        super(Traceroute, self).__init__()
        self.ip_address = ip_address
        self.source = source
        if self.source is None:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            json_file = open(os.path.join(cur_dir, "sources.json"), "r").read()
            sources = json.loads(json_file.replace("_IP_ADDRESS_", ip_address))
            self.source = sources[country]
        self.country = country
        self.tmp_dir = tmp_dir
        self.no_geo = no_geo
        self.timeout = timeout
        self.debug = debug
        self.locations = {}

    def traceroute(self):
        """
        Instead of running the actual traceroute command, we will fetch
        standard traceroute results from several publicly available webpages
        that are listed at traceroute.org. For each hop, we will then attach
        geolocation information to it.
        """
        self.print_debug("ip_address={}".format(self.ip_address))

        filename = "{}.{}.txt".format(self.ip_address, self.country)
        filepath = os.path.join(self.tmp_dir, filename)

        if not os.path.exists(filepath):
            if self.country == "LO":
                status_code, traceroute = self.execute_cmd(self.source['url'])
            else:
                status_code, traceroute = self.get_traceroute_output()
            if status_code != 0 and status_code != 200:
                return {'error': status_code}
            open(filepath, "w").write(traceroute)
        traceroute = open(filepath, "r").read()

        # hop_num, hosts
        hops = self.get_hops(traceroute)

        # hop_num, hostname, ip_address, rtt
        hops = self.get_formatted_hops(hops)

        if not self.no_geo:
            # hop_num, hostname, ip_address, rtt, latitude, longitude
            hops = self.get_geocoded_hops(hops)

        return hops

    def get_traceroute_output(self):
        """
        Fetches traceroute output from a webpage.
        """
        url = self.source['url']
        if 'post_data' in self.source:
            context = self.source['post_data']
        else:
            context = None
        status_code, content = self.urlopen(url, context=context)
        content = content.strip()
        regex = r'<pre.*?>(?P<traceroute>.*?)</pre>'
        pattern = re.compile(regex, re.DOTALL | re.IGNORECASE)
        matches = re.findall(pattern, content)
        if not matches:
            # Manually append closing </pre> for partially downloaded page
            content = "{}</pre>".format(content)
            matches = re.findall(pattern, content)
        traceroute = ''
        for match in matches:
            match = match.strip()
            if match and 'ms' in match.lower():
                traceroute = match
                break
        return (status_code, traceroute)

    def get_hops(self, traceroute):
        """
        Returns hops from traceroute output in an array of dicts each
        with hop number and the associated hosts data.
        """
        hops = []
        regex = r'^(?P<hop_num>\d+)(?P<hosts>.*?)$'
        lines = traceroute.split("\n")
        for line in lines:
            line = line.strip()
            hop = {}
            if not line:
                continue
            try:
                hop = re.match(regex, line).groupdict()
            except AttributeError:
                continue
            self.print_debug(hop)
            hops.append(hop)
        return hops

    def get_formatted_hops(self, hops):
        """
        Hosts data from get_hops() is represented in a single string.
        We use this function to better represent the hosts data in a dict.
        """
        formatted_hops = []
        # Regex to extract "IP/HOSTNAME (HOSTNAME/IP) [optional ASN] RTT ms".
        regex = r'(' \
                r'(?P<i1>[\d.]+) \((?P<h1>[\w.-]+)\)(?P<a1>[\s\w\[\]]*)' \
                r'|' \
                r'(?P<h2>[\w.-]+) \((?P<i2>[\d.]+)\)(?P<a2>[\s\w\[\]]*)' \
                r')' \
                r' (?P<r>\d{1,4}.\d{1,4}\s{0,1}ms)'
        for hop in hops:
            hop_num = int(hop['hop_num'].strip())
            hosts = hop['hosts'].replace("  ", " ").strip()
            # Using re.finditer(), we split the hosts, then for each host,
            # we store a tuple of hostname, IP address and the first RTT.
            hosts = re.finditer(regex, hosts)
            for host in hosts:
                hop_context = {
                    'hop_num': hop_num,
                    'hostname': host.group('h1') or host.group('h2'),
                    'ip_address': host.group('i1') or host.group('i2'),
                    'rtt': host.group('r'),
                }
                self.print_debug(hop_context)
                formatted_hops.append(hop_context)
        return formatted_hops

    def get_geocoded_hops(self, hops):
        """
        Returns hops from get_formatted_hops() with geolocation information
        for each hop.
        """
        geocoded_hops = []
        for hop in hops:
            ip_address = hop['ip_address']
            location = None
            if ip_address in self.locations:
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
        """
        Returns geolocation information for the given IP address.
        """
        location = None
        url = "http://dazzlepod.com/ip/{}.json".format(ip_address)
        status_code, json_data = self.urlopen(url)
        if status_code == 200 and json_data:
            tmp_location = json.loads(json_data)
            if 'latitude' in tmp_location and 'longitude' in tmp_location:
                location = tmp_location
        return location

    def execute_cmd(self, cmd):
        """
        Executes given command using subprocess.Popen().
        """
        stdout = ""
        returncode = -1
        process = Popen(cmd, shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        try:
            signal.signal(signal.SIGALRM, self.signal_handler)
            signal.alarm(self.timeout)
            stdout, stderr = process.communicate()
            returncode = process.returncode
            self.print_debug("cmd={}, returncode={}".format(cmd, returncode))
            if returncode != 0:
                self.print_debug("stderr={}".format(stderr))
            signal.alarm(0)
        except Exception as err:
            self.print_debug(str(err))
        return (returncode, stdout)

    def urlopen(self, url, context=None):
        """
        Fetches webpage.
        """
        status_code = 200
        request = urllib2.Request(url=url)
        request.add_header('User-Agent', USER_AGENT)
        if context:
            data = urllib.urlencode(context)
            request.add_data(data)
        content = ""
        try:
            response = urllib2.urlopen(request)
            self.print_debug("url={}".format(response.geturl()))
            content = self.chunked_read(response)
        except urllib2.HTTPError as err:
            status_code = err.code
        except urllib2.URLError:
            pass
        return (status_code, content)

    def chunked_read(self, response):
        """
        Fetches response in chunks. A signal handler is attached to abort
        reading after set timeout.
        """
        content = ""
        max_bytes = 1 * 1024 * 1024  # Max. page size = 1MB
        read_bytes = 0
        bytes_per_read = 64  # Chunk size = 64 bytes
        try:
            signal.signal(signal.SIGALRM, self.signal_handler)
            signal.alarm(self.timeout)
            while read_bytes <= max_bytes:
                data = response.read(bytes_per_read)
                if not data:
                    break
                content += data
                read_bytes += bytes_per_read
                self.print_debug("read_bytes={}, {}".format(read_bytes, data))
            signal.alarm(0)
        except Exception as err:
            self.print_debug(str(err))
        return content

    def signal_handler(self, signum, frame):
        """
        Raises exception when signal is caught.
        """
        raise Exception("Caught signal {}".format(signum))

    def print_debug(self, msg):
        """
        Prints debug message to standard output.
        """
        if self.debug:
            print("[DEBUG {}] {}".format(datetime.datetime.now(), msg))


def main():
    cmdparser = optparse.OptionParser("%prog --ip_address=IP_ADDRESS")
    cmdparser.add_option(
        "-i", "--ip_address", type="string", default="8.8.8.8",
        help="IP address of destination host (default: 8.8.8.8)")
    cmdparser.add_option(
        "-j", "--json_file", type="string", default="sources.json",
        help="List of sources in JSON file (default: sources.json)")
    cmdparser.add_option(
        "-c", "--country", type="choice", default="US",
        choices=["LO", "AU", "CH", "JP", "RU", "UK", "US"],
        help=("Traceroute will be initiated from this country; choose "
              "'LO' for localhost to run traceroute locally, "
              "'AU' for Australia, "
              "'CH' for Switzerland, "
              "'JP' for Japan, "
              "'RU' for Russia, "
              "'UK' for United Kingdom or "
              "'US' for United States (default: US)"))
    cmdparser.add_option(
        "-t", "--tmp_dir", type="string", default="/tmp",
        help=("Temporary directory to store downloaded traceroute results "
              "(default: /tmp)"))
    cmdparser.add_option(
        "-n", "--no_geo", action="store_true", default=False,
        help="No geolocation data (default: False)")
    cmdparser.add_option(
        "-s", "--timeout", type="int", default=120,
        help="Timeout in seconds for all downloads (default: 120)")
    cmdparser.add_option(
        "-d", "--debug", action="store_true", default=False,
        help="Show debug output (default: False)")
    options, _ = cmdparser.parse_args()
    json_file = open(options.json_file, "r").read()
    sources = json.loads(json_file.replace("_IP_ADDRESS_", options.ip_address))
    traceroute = Traceroute(ip_address=options.ip_address,
                            source=sources[options.country],
                            country=options.country,
                            tmp_dir=options.tmp_dir,
                            no_geo=options.no_geo,
                            timeout=options.timeout,
                            debug=options.debug)
    hops = traceroute.traceroute()
    print(json.dumps(hops, indent=4))
    return 0


if __name__ == '__main__':
    sys.exit(main())
