# -*- encoding: utf-8 -*-
'''
Hubble Nova plugin for running miscellaneous one-off python functions to
run more complex nova audits without allowing arbitrary command execution
from within the yaml profiles.

:maintainer: HubbleStack / basepi
:maturity: 2016.7.2
:platform: All
:requires: SaltStack

Sample YAML data, with inline comments:

# Top level key lets the module know it should look at this data
misc:
  # Unique ID for this set of audits
  nodev:
    data:
      # 'osfinger' grain, for multiplatform support
      'Red Hat Enterprise Linux Server-6':
        # tag is required
        tag: CIS-1.1.10
        function: misc_function_name
        args: # optional
          - first_arg
          - second_arg
        kwargs: # optional
          first_kwarg: value
          second_kwarg: value

      # Catch-all, if no other osfinger match was found
      '*':
        tag: generic_tag
        function: misc_function_name
        args: # optional
          - first_arg
          - second_arg
        kwargs: # optional
          first_kwarg: value
          second_kwarg: value
    # Description will be output with the results
    description: '/home should be nodev'
'''
from __future__ import absolute_import
import logging

import fnmatch
import yaml
import os
import copy
import re
import salt.utils
from salt.ext import six
from collections import Counter

log = logging.getLogger(__name__)


def __virtual__():
    return True


def audit(data_list, tags, debug=False, **kwargs):
    '''
    Run the misc audits contained in the data_list
    '''
    __data__ = {}
    for profile, data in data_list:
        _merge_yaml(__data__, data, profile)
    __tags__ = _get_tags(__data__)

    if debug:
        log.debug('misc audit __data__:')
        log.debug(__data__)
        log.debug('misc audit __tags__:')
        log.debug(__tags__)

    ret = {'Success': [], 'Failure': [], 'Controlled': []}
    for tag in __tags__:
        if fnmatch.fnmatch(tag, tags):
            for tag_data in __tags__[tag]:
                if 'control' in tag_data:
                    ret['Controlled'].append(tag_data)
                    continue
                if 'function' not in tag_data:
                    continue

                function = FUNCTION_MAP.get(tag_data['function'])
                if not function:
                    if 'Errors' not in ret:
                        ret['Errors'] = []
                    ret['Errors'].append({tag: 'No function {0} found'
                                              .format(tag_data['function'])})
		    continue
                args = tag_data.get('args', [])
                kwargs = tag_data.get('kwargs', {})

                # Call the function
                result = function(*args, **kwargs)

                if result is True:
                    ret['Success'].append(tag_data)
                elif isinstance(result, six.string_types):
                    tag_data['failure_reason'] = result
                    ret['Failure'].append(tag_data)
                else:
                    ret['Failure'].append(tag_data)

    return ret


def _merge_yaml(ret, data, profile=None):
    '''
    Merge two yaml dicts together at the misc level
    '''
    if 'misc' not in ret:
        ret['misc'] = []
    if 'misc' in data:
        for key, val in data['misc'].iteritems():
            if profile and isinstance(val, dict):
                val['nova_profile'] = profile
            ret['misc'].append({key: val})
    return ret


def _get_tags(data):
    '''
    Retrieve all the tags for this distro from the yaml
    '''
    ret = {}
    distro = __grains__.get('osfinger')
    for audit_dict in data.get('misc', []):
        # misc:0
        for audit_id, audit_data in audit_dict.iteritems():
            # misc:0:nodev
            tags_dict = audit_data.get('data', {})
            # misc:0:nodev:data
            tags = None
            for osfinger in tags_dict:
                if osfinger == '*':
                    continue
                osfinger_list = [finger.strip() for finger in osfinger.split(',')]
                for osfinger_glob in osfinger_list:
                    if fnmatch.fnmatch(distro, osfinger_glob):
                        tags = tags_dict.get(osfinger)
                        break
                if tags is not None:
                    break
            # If we didn't find a match, check for a '*'
            if tags is None:
                tags = tags_dict.get('*', {})
            # misc:0:nodev:data:Debian-8
            if 'tag' not in tags:
                tags['tag'] = ''
            tag = tags['tag']
            if tag not in ret:
                ret[tag] = []
            formatted_data = {'tag': tag,
                              'module': 'misc'}
            formatted_data.update(audit_data)
            formatted_data.update(tags)
            formatted_data.pop('data')
            ret[tag].append(formatted_data)
    return ret


############################
# Begin function definitions
############################

def _is_valid_home_directory(directory_path):
    directory_path = None if directory_path is None else directory_path.strip()
    if directory_path is not None and directory_path != "" and os.path.isdir(directory_path) and directory_path != "/":
	return True

    return False

def _execute_shell_command(cmd):
    '''
    This function will execute passed command in /bin/shell
    '''
    return __salt__['cmd.run'](cmd, python_shell=True, shell='/bin/bash', ignore_retcode=True)

def check_all_ports_firewall_rules(reason=''):
    '''
    Ensure firewall rule for all open ports
    '''
    start_open_ports = (_execute_shell_command('netstat -ln | grep "Active Internet connections (only servers)" -n | cut -d ":" -f1')).strip()
    end_open_ports = (_execute_shell_command('netstat -ln | grep "Active UNIX domain sockets (only servers)" -n  | cut -d ":" -f1')).strip()
    open_ports = (_execute_shell_command('netstat -ln | awk \'FNR > ' + start_open_ports + ' && FNR < ' + end_open_ports + ' && $6 == "LISTEN" && $4 !~ /127.0.0.1/ {print $4}\' | sed -e "s/.*://"')).strip()
    open_ports = open_ports.split('\n') if open_ports != "" else []
    firewall_ports = (_execute_shell_command('iptables -L INPUT -v -n | awk \'FNR > 2 && $11 != "" && $11 ~ /^dpt:/ {print $11}\' | sed -e "s/.*://"')).strip()
    firewall_ports = firewall_ports.split('\n') if firewall_ports != "" else []
    no_firewall_ports = []

    for open_port in open_ports:
        if open_port not in firewall_ports:
            no_firewall_ports.append(open_port)

    if len(no_firewall_ports) == 0:
        return True
    return str(no_firewall_ports)

def check_password_fields_not_empty(reason=''):
    '''
    Ensure password fields are not empty
    '''
    result = _execute_shell_command('cat /etc/shadow | awk -F: \'($2 == "" ) { print $1 " does not have a password "}\'')
    if result == '':
      return True
    return result

def ungrouped_files_or_dir(reason=''):
    '''
    Ensure no ungrouped files or directories exist
    '''
    result = _execute_shell_command('df --local -P | awk {\'if (NR!=1) print $6\'} | xargs -I \'{}\' find \'{}\' -xdev -nogroup')
    if result == '':
      return True
    return result

def unowned_files_or_dir(reason=''):
    '''
    Ensure no unowned files or directories exist
    '''
    result = _execute_shell_command('df --local -P | awk {\'if (NR!=1) print $6\'} | xargs -I \'{}\' find \'{}\' -xdev -nouser')
    if result == '':
      return True
    return result

def world_writable_file(reason=''):
    '''
    Ensure no world writable files exist
    '''
    result = _execute_shell_command('df --local -P | awk {\'if (NR!=1) print $6\'} | xargs -I \'{}\' find \'{}\' -xdev -type f -perm -0002')
    if result == '':
      return True
    return result

def system_account_non_login(reason=''):
    '''
    Ensure system accounts are non-login
    '''
    result = _execute_shell_command('egrep -v "^\+" /etc/passwd | awk -F: \'($1!="root" && $1!="sync" && $1!="shutdown" && $1!="halt" && $3<500 && $7!="/sbin/nologin" && $7!="/bin/false") {print}\'')
    if result == '':
      return True
    return result

def sticky_bit_on_world_writable_dirs(reason=''):
    '''
    Ensure sticky bit is set on all world-writable directories
    '''
    result = _execute_shell_command('df --local -P | awk {\'if (NR!=1) print $6\'} | xargs -I \'{}\' find \'{}\' -xdev -type d \( -perm -0002 -a ! -perm -1000 \) 2>/dev/null')
    if result == '':
      return True
    return result

def default_group_for_root(reason=''):
    '''
    Ensure default group for the root account is GID 0
    '''
    result = _execute_shell_command('grep "^root:" /etc/passwd | cut -f4 -d:')
    result = result.strip()
    if result == '0':
      return True
    return False

def root_is_only_uid_0_account(reason=''):
    '''
    Ensure root is the only UID 0 account
    '''
    result = _execute_shell_command('cat /etc/passwd | awk -F: \'($3 == 0) { print $1 }\'')
    if result.strip() == 'root':
      return True
    return result

def test_success():
    '''
    Automatically returns success
    '''
    return True


def test_failure():
    '''
    Automatically returns failure, no reason
    '''
    return False


def test_failure_reason(reason):
    '''
    Automatically returns failure, with a reason (first arg)
    '''
    return reason

def test_mount_attrs(mount_name,attribute,check_type='hard'):
    '''
    Ensure that a given directory is mounted with appropriate attributes
    If check_type is soft, then in absence of volume, True will be returned
    If check_type is hard, then in absence of volume, False will be returned
    '''
    #check that the path exists on system
    command = 'test -e ' + mount_name + ' ; echo $?'
    output = _execute_shell_command( command)
    if output.strip() == '1':
        return True if check_type == "soft" else (mount_name + " folder does not exist")

    #if the path exits, proceed with following code
    output = _execute_shell_command('mount | grep ' + mount_name)
    if output.strip() == '':
	return True if check_type == "soft" else (mount_name + " is not mounted")
    elif attribute not in output:
        return str(output)
    else:
        return True

def check_time_synchronization(reason=''):
    '''
    Ensure that some service is running to synchronize the system clock
    '''
    command = 'systemctl status systemd-timesyncd ntpd | grep "Active: active (running)"'
    output = _execute_shell_command( command )
    if output.strip() == '':
        return "neither ntpd nor timesyncd is running"
    else:
        return True


def restrict_permissions(path,permission):
    '''
    Ensure that the file permissions on path are equal or more strict than the  pemissions given in argument
    '''
    path_details = __salt__['file.stats'](path)
    given_permission = path_details.get('mode')
    given_permission = given_permission[-3:]
    max_permission = str(permission)
    if (_is_permission_in_limit(max_permission[0],given_permission[0]) and _is_permission_in_limit(max_permission[1],given_permission[1]) and _is_permission_in_limit(max_permission[2],given_permission[2])):
        return True
    return given_permission


def _is_permission_in_limit(max_permission,given_permission):
    '''
    Return true only if given_permission is not more linient that max_permission. In other words, if 
    r or w or x is present in given_permission but absent in max_permission, it should return False 
    Takes input two integer values from 0 to 7.
    '''    
    max_permission = int(max_permission)
    given_permission = int(given_permission)
    allowed_r = False
    allowed_w = False
    allowed_x = False
    given_r = False
    given_w = False
    given_x = False

    if max_permission >= 4:
        allowed_r = True
        max_permission = max_permission - 4
    if max_permission >= 2:
        allowed_w = True
        max_permission = max_permission - 2
    if max_permission >= 1:
        allowed_x = True

    if given_permission >= 4:
        given_r = True
        given_permission = given_permission - 4
    if given_permission >= 2:
        given_w = True
        given_permission = given_permission - 2
    if given_permission >= 1:
        given_x = True

    if given_r and ( not allowed_r ):
        return False
    if given_w and ( not allowed_w ):
        return False
    if given_x and ( not allowed_x ):
        return False

    return True
        

def check_path_integrity(reason=''):
    '''
    Ensure that system PATH variable is not malformed.
    ''' 

    script = """
    if [ "`echo $PATH | grep ::`" != "" ]; then 
        echo "Empty Directory in PATH (::)" 
    fi 

    if [ "`echo $PATH | grep :$`" != "" ]; then 
        echo "Trailing : in PATH" 
    fi 

    p=`echo $PATH | sed -e 's/::/:/' -e 's/:$//' -e 's/:/ /g'` 
    set -- $p 
    while [ "$1" != "" ]; do 
        if [ "$1" = "." ]; then 
            echo "PATH contains ." 
            shift 
            continue 
        fi 
        
        if [ -d $1 ]; then 
            dirperm=`ls -ldH $1 | cut -f1 -d" "` 
            if [ `echo $dirperm | cut -c6` != "-" ]; then 
                echo "Group Write permission set on directory $1" 
            fi 
            if [ `echo $dirperm | cut -c9` != "-" ]; then 
                echo "Other Write permission set on directory $1" 
            fi 
            dirown=`ls -ldH $1 | awk '{print $3}'` 
            if [ "$dirown" != "root" ] ; then 
                echo $1 is not owned by root
            fi 
            else 
            echo $1 is not a directory 
        fi 
        shift 
    done

    """
    output = _execute_shell_command(script)
    if output.strip() == '':
        return True
    else:
        return output


def check_duplicate_uids(reason=''):
    '''
    Return False if any duplicate user id exist in /etc/group file, else return True
    '''
    uids = _execute_shell_command("cat /etc/passwd | cut -f3 -d\":\"").strip()
    uids = uids.split('\n') if uids != "" else []
    duplicate_uids = [k for k,v in Counter(uids).items() if v>1]
    if duplicate_uids is None or duplicate_uids == []:
	return True

    return str(duplicate_uids)


def check_duplicate_gids(reason=''):
    '''
    Return False if any duplicate group id exist in /etc/group file, else return True
    '''
    gids = _execute_shell_command("cat /etc/group | cut -f3 -d\":\"").strip()
    gids = gids.split('\n') if gids != "" else []
    duplicate_gids = [k for k,v in Counter(gids).items() if v>1]
    if duplicate_gids is None or duplicate_gids == []:
	return True

    return str(duplicate_gids)


def check_duplicate_unames(reason=''):
    '''
    Return False if any duplicate user names exist in /etc/group file, else return True
    '''
    unames = _execute_shell_command("cat /etc/passwd | cut -f1 -d\":\"").strip()
    unames = unames.split('\n') if unames != "" else []
    duplicate_unames = [k for k,v in Counter(unames).items() if v>1]
    if duplicate_unames is None or duplicate_unames == []:
	return True

    return str(duplicate_unames)


def check_duplicate_gnames(reason=''):
    '''
    Return False if any duplicate group names exist in /etc/group file, else return True
    '''
    gnames = _execute_shell_command("cat /etc/group | cut -f1 -d\":\"").strip()
    gnames = gnames.split('\n') if gnames != "" else []
    duplicate_gnames = [k for k,v in Counter(gnames).items() if v>1]
    if duplicate_gnames is None or duplicate_gnames == []:
	return True

    return str(duplicate_gnames)


def check_directory_files_permission(path,permission):
    '''
    Check all files permission inside a directory
    '''
    files_list = _execute_shell_command("find /var/log -type f").strip()
    files_list = files_list.split('\n') if files_list != "" else []
    bad_permission_files = []
    for file_in_directory in files_list:
	per = restrict_permissions(file_in_directory, permission)
	if per is not True:
		bad_permission_files += [file_in_directory + ": Bad Permission - " + per + ":"]

    if bad_permission_files == []:
    	return True

    return str(bad_permission_files)


def check_core_dumps(reason=''):
    '''
    Ensure core dumps are restricted
    '''
    hard_core_dump_value = _execute_shell_command("grep -R -E \"hard +core\" /etc/security/limits.conf /etc/security/limits.d/ | awk '{print $4}'").strip()
    hard_core_dump_value = hard_core_dump_value.split('\n') if hard_core_dump_value != "" else []
    if '0' in hard_core_dump_value:
	return True
    
    if hard_core_dump_value is None or hard_core_dump_value == [] or hard_core_dump_value == "":
	return "'hard core' not found in any file"

    return str(hard_core_dump_value)


def check_service_status(service_name, state):
    '''
    Ensure that the given service is in the required state. Return False if it is not in desired state
    Return True otherwise
    state can be enabled or disabled.
    '''
    output = _execute_shell_command('systemctl is-enabled ' + service_name + ' >/dev/null 2>&1; echo $?')
    if (state == "disabled" and output.strip() == "1") or (state == "enabled" and output.strip() == "0"):
        return True
    else:
        return _execute_shell_command('systemctl is-enabled ' + service_name + ' 2>/dev/null')

def check_ssh_timeout_config(reason=''):
    '''
    Ensure SSH Idle Timeout Interval is configured
    '''

    client_alive_interval = _execute_shell_command("grep \"^ClientAliveInterval\" /etc/ssh/sshd_config | awk '{print $NF}'").strip()
    if client_alive_interval != '' and int(client_alive_interval) <= 300:
    	client_alive_count_max = _execute_shell_command("grep \"^ClientAliveCountMax\" /etc/ssh/sshd_config | awk '{print $NF}'").strip()
    	if client_alive_count_max != '' and int(client_alive_count_max) <= 3:
       	    return True
    	else:
            return "ClientAliveCountMax value should be less than equal to 3"
    else:
    	return "ClientAliveInterval value should be less than equal to 300"


def check_unowned_files(reason=''):
    '''
    Ensure no unowned files or directories exist
    '''

    unowned_files = _execute_shell_command("df --local -P | awk 'NR!=1 {print $6}' | xargs -I '{}' find '{}' -xdev -nouser 2>/dev/null").strip()
    unowned_files = unowned_files.split('\n') if unowned_files != "" else []
    # The command above only searches local filesystems, there may still be compromised items on network mounted partitions.
    # Following command will check each partition for unowned files
    unowned_partition_files = _execute_shell_command("mount | awk '{print $3}' | xargs -I '{}' find '{}' -xdev -nouser 2>/dev/null").strip()
    unowned_partition_files = unowned_partition_files.split('\n') if unowned_partition_files != "" else []
    unowned_files = unowned_files + unowned_partition_files
    if unowned_files == []:
        return True

    return str(list(set(unowned_files)))


def check_ungrouped_files(reason=''):
    '''
    Ensure no ungrouped files or directories exist
    '''

    ungrouped_files = _execute_shell_command("df --local -P | awk 'NR!=1 {print $6}' | xargs -I '{}' find '{}' -xdev -nogroup 2>/dev/null").strip()
    ungrouped_files = ungrouped_files.split('\n') if ungrouped_files != "" else []
    # The command above only searches local filesystems, there may still be compromised items on network mounted partitions.
    # Following command will check each partition for unowned files
    ungrouped_partition_files = _execute_shell_command("mount | awk '{print $3}' | xargs -I '{}' find '{}' -xdev -nogroup 2>/dev/null").strip()
    ungrouped_partition_files = ungrouped_partition_files.split('\n') if ungrouped_partition_files != "" else []
    ungrouped_files = ungrouped_files + ungrouped_partition_files
    if ungrouped_files == []:
        return True

    return str(list(set(ungrouped_files)))


def check_all_users_home_directory(max_system_uid):
    '''
    Ensure all users' home directories exist
    '''

    max_system_uid = int(max_system_uid)
    users_uids_dirs = _execute_shell_command("cat /etc/passwd | awk -F: '{ print $1 \" \" $3 \" \" $6 }'").strip()
    users_uids_dirs = users_uids_dirs.split('\n') if users_uids_dirs != "" else []
    error = []
    for user_data in users_uids_dirs:
        user_uid_dir = user_data.strip().split(" ")
        if len(user_uid_dir) < 3:
                user_uid_dir = user_uid_dir + ['']*(3-len(user_uid_dir))
        if user_uid_dir[1].isdigit():
            if not _is_valid_home_directory(user_uid_dir[2]) and int(user_uid_dir[1]) >= max_system_uid and user_uid_dir[0] is not "nfsnobody":
                error += ["Either home directory " + user_uid_dir[2] + " of user " + user_uid_dir[0] + " is invalid or does not exist."]
        else:
            error += ["User " + user_uid_dir[0] + " has invalid uid " + user_uid_dir[1]]
    if error == []:
        return True

    return str(error)


def check_users_home_directory_permissions(reason=''):
    '''
    Ensure users' home directories permissions are 750 or more restrictive
    '''

    users_dirs = _execute_shell_command("cat /etc/passwd | egrep -v '(root|halt|sync|shutdown)' | awk -F: '($7 != \"/sbin/nologin\") {print $1\" \"$6}'").strip()
    users_dirs = users_dirs.split('\n') if users_dirs != "" else []
    error = []
    for user_dir in users_dirs:
        user_dir = user_dir.split(" ")
        if len(user_dir) < 2:
                user_dir = user_dir + ['']*(2-len(user_dir))
        if not _is_valid_home_directory(user_dir[1]):
            error += ["Either home directory " + user_dir[1] + " of user " + user_dir[0] + " is invalid or does not exist."]
        else:
            result = restrict_permissions(user_dir[1], "750")
            if result is not True:
                error += ["permission on home directory " + user_dir[1]  + " of user " + user_dir[0] + " is wrong: " + result]

    if error == []:
        return True

    return str(error)


def check_users_own_their_home(max_system_uid):
    '''
    Ensure users own their home directories
    '''

    max_system_uid = int(max_system_uid)

    users_uids_dirs = _execute_shell_command("cat /etc/passwd | awk -F: '{ print $1 \" \" $3 \" \" $6 }'").strip()
    users_uids_dirs = users_uids_dirs.split('\n') if users_uids_dirs != "" else []
    error = []
    for user_data in users_uids_dirs:
        user_uid_dir = user_data.strip().split(" ")
        if len(user_uid_dir) < 3:
                user_uid_dir = user_uid_dir + ['']*(3-len(user_uid_dir))
        if user_uid_dir[1].isdigit():
	    if not _is_valid_home_directory(user_uid_dir[2]):
		error += ["Either home directory " + user_uid_dir[2] + " of user " + user_uid_dir[0] + " is invalid or does not exist."]
            elif int(user_uid_dir[1]) >= max_system_uid and user_uid_dir[0] is not "nfsnobody":
                owner = _execute_shell_command("stat -L -c \"%U\" \"" + user_uid_dir[2] + "\"")
                if owner is not user_uid_dir[0]:
                    error += ["The home directory " + user_uid_dir[2] + " of user " + user_uid_dir[0] + " is owned by " + owner]
        else:
            error += ["User " + user_uid_dir[0] + " has invalid uid " + user_uid_dir[1]]

    if error == []:
        return True

    return str(error)


def check_users_dot_files(reason=''):
    '''
    Ensure users' dot files are not group or world writable
    '''

    users_dirs = _execute_shell_command("cat /etc/passwd | egrep -v '(root|halt|sync|shutdown)' | awk -F: '($7 != \"/sbin/nologin\") {print $1\" \"$6}'").strip()
    users_dirs = users_dirs.split('\n') if users_dirs != "" else []
    error = []
    for user_dir in users_dirs:
        user_dir = user_dir.split()
        if len(user_dir) < 2:
                user_dir = user_dir + ['']*(2-len(user_dir))
        if not _is_valid_home_directory(user_dir[1]):
            error += ["Either home directory " + user_dir[1] + " of user " + user_dir[0] + " is invalid or does not exist."]
        else:
            dot_files = _execute_shell_command("find " + user_dir[1] + " -name \".*\"").strip()
            dot_files = dot_files.split('\n') if dot_files != "" else []
            for dot_file in dot_files:
                if os.path.isfile(dot_file):
                    file_permission = _execute_shell_command("ls -ld " + dot_file + " | cut -f1 -d\" \"").strip()
                    if file_permission[5] is "w":
                        error += ["Group Write permission set on file " + dot_file + " for user " + user_dir[0]]
                    if file_permission[8] is "w":
                        error += ["Other Write permission set on file " + dot_file + " for user " + user_dir[0]]

    if error == []:
        return True

    return str(error)


def check_users_forward_files(reason=''):
    '''
    Ensure no users have .forward files
    '''

    users_dirs = _execute_shell_command("cat /etc/passwd | awk -F: '{ print $1\" \"$6 }'").strip()
    users_dirs = users_dirs.split('\n') if users_dirs != "" else []
    error = []
    for user_dir in users_dirs:
        user_dir = user_dir.split()
        if len(user_dir) < 2:
                user_dir = user_dir + ['']*(2-len(user_dir))
        if not _is_valid_home_directory(user_dir[1]):
            error += ["Either home directory " + user_dir[1] + " of user " + user_dir[0] + " is invalid or does not exist."]
        else:
            forward_file = _execute_shell_command("find " + user_dir[1] + " -name \".forward\"").strip()
            if forward_file is not None and os.path.isfile(forward_file):
                error += ["Home directory: " + user_dir[1] + ", for user: " + user_dir[0] + " has " + forward_file + " file"]

    if error == []:
        return True

    return str(error)


def check_users_netrc_files(reason=''):
    '''
    Ensure no users have .netrc files
    '''

    users_dirs = _execute_shell_command("cat /etc/passwd | awk -F: '{ print $1\" \"$6 }'").strip()
    users_dirs = users_dirs.split('\n') if users_dirs != "" else []
    error = []
    for user_dir in users_dirs:
        user_dir = user_dir.split()
        if len(user_dir) < 2:
                user_dir = user_dir + ['']*(2-len(user_dir))
        if not _is_valid_home_directory(user_dir[1]):
            error += ["Either home directory " + user_dir[1] + " of user " + user_dir[0] + " is invalid or does not exist."]
        else:
            netrc_file = _execute_shell_command("find " + user_dir[1] + " -name \".netrc\"").strip()
            if netrc_file is not None and os.path.isfile(netrc_file):
                error += ["Home directory: " + user_dir[1] + ", for user: " + user_dir[0] + " has .netrc file"]

    if error == []:
        return True

    return str(error)


def check_groups_validity(reason=''):
    '''
    Ensure all groups in /etc/passwd exist in /etc/group
    '''

    group_ids_in_passwd = _execute_shell_command("cut -s -d: -f4 /etc/passwd 2>/dev/null").strip()
    group_ids_in_passwd = group_ids_in_passwd.split('\n') if group_ids_in_passwd != "" else []
    group_ids_in_group = _execute_shell_command("cut -s -d: -f3 /etc/group 2>/dev/null").strip()
    group_ids_in_group = group_ids_in_group.split('\n') if group_ids_in_group != "" else []
    invalid_group_ids = list(set(group_ids_in_passwd) - set(group_ids_in_group))

    if invalid_group_ids == []:
        return True

    return "Groups which are referenced by /etc/passwd but does not exist in /etc/group: " + str(invalid_group_ids)


def ensure_reverse_path_filtering(reason=''):
    '''
    Ensure Reverse Path Filtering is enabled
    '''
    error_list = []
    command = "sysctl net.ipv4.conf.all.rp_filter 2> /dev/null"
    output = _execute_shell_command(command)
    if output.strip() == '':
        error_list.append( "net.ipv4.conf.all.rp_filter not found")
    search_results = re.findall("rp_filter = (\d+)",output)
    result = int(search_results[0])
    if( result < 1):
        error_list.append( "net.ipv4.conf.all.rp_filter  value set to " + str(result))
    command = "sysctl net.ipv4.conf.default.rp_filter 2> /dev/null"  
    output = _execute_shell_command(command)
    if output.strip() == '':
        error_list.append( "net.ipv4.conf.default.rp_filter not found")
    search_results = re.findall("rp_filter = (\d+)",output)
    result = int(search_results[0])
    if( result < 1):
        error_list.append( "net.ipv4.conf.default.rp_filter  value set to " + str(result))
    if len(error_list) > 0 :
        return str(error_list)
    else:
        return True


FUNCTION_MAP = {
    'check_all_ports_firewall_rules': check_all_ports_firewall_rules,
    'check_password_fields_not_empty': check_password_fields_not_empty,
    'ungrouped_files_or_dir': ungrouped_files_or_dir,
    'unowned_files_or_dir': unowned_files_or_dir,
    'world_writable_file': world_writable_file,
    'system_account_non_login': system_account_non_login,
    'sticky_bit_on_world_writable_dirs': sticky_bit_on_world_writable_dirs,
    'default_group_for_root': default_group_for_root,
    'root_is_only_uid_0_account': root_is_only_uid_0_account,
    'test_success': test_success,
    'test_failure': test_failure,
    'test_failure_reason': test_failure_reason,
    'test_mount_attrs' : test_mount_attrs,
    'check_path_integrity' : check_path_integrity,
    'restrict_permissions' : restrict_permissions,
    'check_time_synchronization' : check_time_synchronization,
    'check_core_dumps': check_core_dumps,
    'check_directory_files_permission': check_directory_files_permission,
    'check_duplicate_gnames': check_duplicate_gnames,
    'check_duplicate_unames': check_duplicate_unames,
    'check_duplicate_gids': check_duplicate_gids,
    'check_duplicate_uids': check_duplicate_uids,
    'check_service_status' : check_service_status,
    'check_ssh_timeout_config': check_ssh_timeout_config,
    'check_unowned_files': check_unowned_files,
    'check_ungrouped_files': check_ungrouped_files,
    'check_all_users_home_directory': check_all_users_home_directory,
    'check_users_home_directory_permissions': check_users_home_directory_permissions,
    'check_users_own_their_home': check_users_own_their_home,
    'check_users_dot_files': check_users_dot_files,
    'check_users_forward_files': check_users_forward_files,
    'check_users_netrc_files': check_users_netrc_files,
    'check_groups_validity': check_groups_validity,
    'ensure_reverse_path_filtering': ensure_reverse_path_filtering,
}
