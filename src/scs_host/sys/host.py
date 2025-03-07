"""
Created on 16 Nov 2016

@author: Bruno Beloff (bruno.beloff@southcoastscience.com)

http://dumb-looks-free.blogspot.co.uk/2014/05/beaglebone-black-bbb-revision-serial.html
"""

import os
import pyudev
import re
import socket

from pathlib import Path
from subprocess import check_output, call, Popen, PIPE, DEVNULL, TimeoutExpired

from scs_core.estate.git_pull import GitPull

from scs_core.sys.disk_usage import DiskUsage
from scs_core.sys.disk_volume import DiskVolume
from scs_core.sys.ipv4_address import IPv4Address
from scs_core.sys.logging import Logging
from scs_core.sys.modem import ModemList, Modem, ModemConnection, SIMList, SIM
from scs_core.sys.network import Networks
from scs_core.sys.node import IoTNode
from scs_core.sys.persistence_manager import FilesystemPersistenceManager
from scs_core.sys.uptime_datum import UptimeDatum


# --------------------------------------------------------------------------------------------------------------------

class Host(IoTNode, FilesystemPersistenceManager):
    """
    TI Sitara AM3358AZCZ100 processor
    """

    OS_ENV_PATH =           'SCS_ROOT_PATH'

    I2C_COMMON =            2

    DFE_EEPROM_ADDR =       0x50
    DFE_UID_ADDR =          0x58


    # ----------------------------------------------------------------------------------------------------------------
    # devices...

    __OPC_SPI_ADDR =        '48030000'                          # hard-coded memory-mapped io address
    __OPC_SPI_DEVICE =      0                                   # hard-coded path

    __NDIR_SPI_ADDR =       '481a0000'                          # hard-coded memory-mapped io address
    __NDIR_SPI_DEVICE =     0                                   # hard-coded path

    __GPS_DEVICE =          1                                   # hard-coded path

    __NDIR_USB_DEVICE =     '/dev/ttyUSB0'                      # hard-coded path (Alphasense USB device)

    __PSU_DEVICE =          5                                   # hard-coded path


    # ----------------------------------------------------------------------------------------------------------------
    # time marker...

    __TIME_SYNCHRONIZED =  "/run/systemd/timesync/synchronized"


    # ----------------------------------------------------------------------------------------------------------------
    # directories and files...

    __DEFAULT_HOME_DIR =    '/home/scs'                         # hard-coded abs path
    __LOCK_DIR =            '/run/lock/southcoastscience'       # hard-coded abs path
    __TMP_DIR =             '/tmp/southcoastscience'            # hard-coded abs path

    __SCS_DIR =             'SCS'                               # hard-coded rel path
    __COMMAND_DIR =         'cmd'                               # hard-coded rel path

    __LATEST_UPDATE =       "latest_update.txt"                 # hard-coded rel path
    __DFE_EEP_IMAGE =       'dfe_cape.eep'                      # hard-coded rel path

    __COMMAND_TIMEOUT =     10                                  # seconds


    # ----------------------------------------------------------------------------------------------------------------
    # host acting as DHCP server...

    __SERVER_IPV4_ADDRESS = None                                # hard-coded abs path


    # ----------------------------------------------------------------------------------------------------------------

    @staticmethod
    def spi_bus(spi_address, spi_device):
        context = pyudev.Context()

        kernel_path = '/ocp/spi@' + spi_address + '/channel@' + str(spi_device)

        for device in context.list_devices(subsystem='spidev'):
            parent = device.parent

            if type(parent) and parent['OF_FULLNAME'] == kernel_path:
                node = device.device_node

                match = re.match(r'\D+(\d+).\d+', node)              # e.g. /dev/spidev1.0

                if match is None:
                    continue

                groups = match.groups()

                return int(groups[0])

        raise OSError("No SPI bus could be found for %s" % kernel_path)


    # ----------------------------------------------------------------------------------------------------------------

    @staticmethod
    def serial_number():
        serial = os.popen("hexdump -e '8/1 \"%c\"' /sys/bus/i2c/devices/0-0050/eeprom -s 16 -n 12").readline()

        return serial


    @staticmethod
    def enable_eeprom_access():
        # nothing needs to be done?
        pass


    @staticmethod
    def shutdown():
        call(['systemctl', 'poweroff', '-i'])


    @classmethod
    def software_update_report(cls):
        git_pull = GitPull.load(cls)

        return None if git_pull is None else str(git_pull.pulled_on.datetime.date())


    # ----------------------------------------------------------------------------------------------------------------

    @classmethod
    def gps_device(cls):
        return cls.__GPS_DEVICE             # we might have to search for it instead


    @classmethod
    def ndir_usb_device(cls):
        return cls.__NDIR_USB_DEVICE        # we might have to search for it instead


    @classmethod
    def psu_device(cls):
        return cls.__PSU_DEVICE             # we might have to search for it instead


    # ----------------------------------------------------------------------------------------------------------------
    # network identity...

    @classmethod
    def name(cls):
        return socket.gethostname()


    @classmethod
    def server_ipv4_address(cls):
        return IPv4Address.construct(cls.__SERVER_IPV4_ADDRESS)


    # ----------------------------------------------------------------------------------------------------------------
    # status...

    @classmethod
    def status(cls):
        return None


    # ----------------------------------------------------------------------------------------------------------------
    # networks and modem...

    @classmethod
    def networks(cls):
        try:
            p = Popen(['nmcli', 'd'], stdout=PIPE, stderr=DEVNULL)
            stdout, _ = p.communicate(timeout=cls.__COMMAND_TIMEOUT)

        except FileNotFoundError:
            return None

        except TimeoutExpired as ex:
            Logging.getLogger().error(repr(ex))
            return None

        if p.returncode != 0:
            return None

        return Networks.construct_from_nmcli(stdout.decode().splitlines())


    @classmethod
    def modem(cls):
        stdout = cls.__modem_list()
        if not stdout:
            return None

        return Modem.construct_from_mmcli(stdout.decode().splitlines())


    @classmethod
    def modem_conn(cls):
        if not cls.__modem_manager_is_enabled():
            return None

        stdout = cls.__modem_list()

        if not stdout:
            return ModemConnection.null_datum()

        return ModemConnection.construct_from_mmcli(stdout.decode().splitlines())


    @classmethod
    def sim(cls):
        stdout = cls.__modem_list()
        if not stdout:
            return None

        sims = SIMList.construct_from_mmcli(stdout.decode().splitlines())
        if len(sims) < 1:
            return None

        # SIM (assume one SIM)...
        p = Popen(['mmcli', '-K', '-i', sims.number(0)], stdout=PIPE, stderr=DEVNULL)
        stdout, _ = p.communicate(timeout=cls.__COMMAND_TIMEOUT)

        if p.returncode != 0:
            return None

        return SIM.construct_from_mmcli(stdout.decode().splitlines())


    @classmethod
    def __modem_manager_is_enabled(cls):
        p = Popen(['systemctl', '-q', 'is-enabled', 'ModemManager.service'], stdout=PIPE, stderr=DEVNULL)
        p.communicate(timeout=cls.__COMMAND_TIMEOUT)

        return p.returncode == 0


    @classmethod
    def __modem_list(cls):
        # ModemList...
        try:
            p = Popen(['mmcli', '-K', '-L'], stdout=PIPE, stderr=DEVNULL)
            stdout, _ = p.communicate(timeout=cls.__COMMAND_TIMEOUT)
        except FileNotFoundError:
            return None

        except TimeoutExpired as ex:
            Logging.getLogger().error(repr(ex))
            return None

        if p.returncode != 0:
            return None

        modems = ModemList.construct_from_mmcli(stdout.decode().splitlines())

        if len(modems) < 1:
            return None

        # Modem (assume one modem)...
        p = Popen(['mmcli', '-K', '-m', modems.number(0)], stdout=PIPE, stderr=DEVNULL)
        stdout, _ = p.communicate(timeout=cls.__COMMAND_TIMEOUT)

        if p.returncode != 0:
            return None

        return stdout


    # ----------------------------------------------------------------------------------------------------------------
    # SPI...

    @classmethod
    def ndir_spi_bus(cls):
        return cls.spi_bus(cls.__NDIR_SPI_ADDR, cls.__NDIR_SPI_DEVICE)


    @classmethod
    def ndir_spi_device(cls):
        return cls.__NDIR_SPI_DEVICE


    @classmethod
    def opc_spi_bus(cls):
        return cls.spi_bus(cls.__OPC_SPI_ADDR, cls.__OPC_SPI_DEVICE)


    @classmethod
    def opc_spi_device(cls):
        return cls.__OPC_SPI_DEVICE


    # ----------------------------------------------------------------------------------------------------------------
    # time...

    @classmethod
    def time_is_synchronized(cls):
        return Path(cls.__TIME_SYNCHRONIZED).exists()


    @classmethod
    def uptime(cls, now=None):
        raw = check_output('uptime')
        report = raw.decode()

        return UptimeDatum.construct_from_report(now, report)


    # ----------------------------------------------------------------------------------------------------------------

    @classmethod
    def disk_volume(cls, mounted_on):
        process = Popen(['df'], stdout=PIPE)
        out, _ = process.communicate()
        rows = out.decode().splitlines()[1:]

        for row in rows:
            volume = DiskVolume.construct_from_df_row(row)

            if volume.mounted_on == mounted_on:
                return volume

        return None


    @classmethod
    def disk_usage(cls, path):
        try:
            statvfs = os.statvfs(path)

        except OSError:
            return None

        return DiskUsage.construct_from_statvfs(path, statvfs)


    # ----------------------------------------------------------------------------------------------------------------
    # tmp directories...

    @classmethod
    def lock_dir(cls):
        return cls.__LOCK_DIR


    @classmethod
    def tmp_dir(cls):
        return cls.__TMP_DIR


    # ----------------------------------------------------------------------------------------------------------------
    # filesystem paths...

    @classmethod
    def home_path(cls):
        return os.environ[cls.OS_ENV_PATH] if cls.OS_ENV_PATH in os.environ else cls.__DEFAULT_HOME_DIR


    @classmethod
    def scs_path(cls):
        return os.path.join(cls.home_path(), cls.__SCS_DIR)


    @classmethod
    def command_path(cls):
        return os.path.join(cls.scs_path(), cls.__COMMAND_DIR)


    @classmethod
    def eep_image(cls):
        return os.path.join(cls.scs_path(), cls.__DFE_EEP_IMAGE)
