#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2021 LG Electronics Inc.
# SPDX-License-Identifier: GPL-3.0-only
import os
import re
import logging
import shutil
import sys
import fosslight_util.constant as constant
import urllib.request
from yaml import safe_dump
from fosslight_util.set_log import init_log
from fosslight_util.spdx_licenses import get_spdx_licenses_json, get_license_from_nick
from fosslight_util.parsing_yaml import find_sbom_yaml_files, parsing_yml
from fosslight_util.output_format import check_output_format
from datetime import datetime
from fosslight_prechecker._precheck import precheck_for_project, precheck_for_files, dump_error_msg, \
                                           get_path_to_find, DEFAULT_EXCLUDE_EXTENSION_FILES
from fosslight_prechecker._result import get_total_file_list
from reuse.header import run as reuse_header
from reuse.download import run as reuse_download
from reuse._comment import EXTENSION_COMMENT_STYLE_MAP_LOWERCASE
from reuse._main import parser as reuse_arg_parser
from reuse.project import Project
from bs4 import BeautifulSoup
from pathlib import Path


PKG_NAME = "fosslight_prechecker"
LICENSE_INCLUDE_FILES = ["license", "license.md", "license.txt", "notice"]
EXCLUDE_DIR = ["test", "tests", "doc", "docs"]
EXCLUDE_PREFIX = ("test", ".", "doc", "__")
OPENSOURCE_LGE_COM_URL_PREFIX = "https://opensource.lge.com/license/"
_result_log = {}
spdx_licenses = []

logger = logging.getLogger(constant.LOGGER_NAME)


def convert_to_spdx_style(input_string):
    input_string = input_string.replace(" ", "-")
    input_converted = f"LicenseRef-{input_string}"
    return input_converted


def check_input_license_format(input_license):
    for spdx in spdx_licenses:
        if input_license.casefold() == spdx.casefold():
            return spdx

    if input_license.startswith('LicenseRef-'):
        return input_license

    licensesfromJson = get_license_from_nick()
    if licensesfromJson == "":
        dump_error_msg(" Error - Return Value to get license from Json is none")

    try:
        # Get frequetly used license from json file
        converted_license = licensesfromJson.get(input_license.casefold())
        if converted_license is None:
            converted_license = convert_to_spdx_style(input_license)
    except Exception as ex:
        dump_error_msg(f"Error - Get frequetly used license : {ex}")

    return converted_license


def check_input_copyright_format(input_copyright):
    regex = re.compile(r'Copyright(\s)+(\(c\)\s)?\s*\d{4}(-\d{4})*(\s)+(\S)+')
    check_ok = True

    if regex.match(input_copyright) is None:
        logger.warning(" You have to input with following format - '<year> <name>'")
        check_ok = False

    return check_ok


def input_license_while_running():
    input_license = ""

    logger.info("# Select a license to write in the license missing files ")
    select = input("\t1.MIT\n \t2.Apache-2.0\n \t3.LGE-Proprietary\n \t4.Manual Input\n \t5.Not select now\n- Choose one from the list: ")
    if select == '1' or select == 'MIT':
        input_license = 'MIT'
    elif select == '2' or select == 'Apache-2.0':
        input_license = 'Apache-2.0'
    elif select == '3' or select == 'LGE Proprietary License':
        input_license = 'LicenseRef-LGE-Proprietary'
    elif select == '4' or select == 'Manually Input':
        input_license = input("   ## Input your License : ")
    elif select == '5' or select == 'Quit' or select == 'quit':
        logger.info(" Not selected any license to write ")
    return input_license


def input_copyright_while_running():
    input_copyright = ""
    input_copyright = input("# Input Copyright to write in the copyright missing files (ex, <year> <name>): ")
    if input_copyright == 'Quit' or input_copyright == 'quit' or input_copyright == 'Q':
        return

    return input_copyright


def set_missing_license_copyright(missing_license_filtered, missing_copyright_filtered, project, path_to_find, license, copyright):
    input_license = ""
    input_copyright = ""

    try:
        main_parser = reuse_arg_parser()
    except Exception as ex:
        dump_error_msg(f"Error_get_arg_parser : {ex}")

    # Print missing License
    if missing_license_filtered is not None and len(missing_license_filtered) > 0:
        missing_license_list = []

        logger.info("# Missing license File(s)")
        for lic_file in sorted(missing_license_filtered):
            logger.info(f"  * {lic_file}")
            missing_license_list.append(os.path.join(path_to_find, lic_file))

        if license == "" and copyright == "":
            input_license = input_license_while_running()
        else:
            input_license = license

        if input_license != "":
            input_license = check_input_license_format(input_license)
            logger.warning(f"  * Your input license : {input_license}")
            parsed_args = main_parser.parse_args(['addheader', '--license', str(input_license)] + missing_license_list)
            try:
                reuse_header(parsed_args, project)
            except Exception as ex:
                dump_error_msg(f"Error_call_run_in_license : {ex}")
    else:
        logger.info("# There is no missing license file\n")

    # Print missing Copyright
    if missing_copyright_filtered is not None and len(missing_copyright_filtered) > 0:
        missing_copyright_list = []

        logger.info("\n# Missing Copyright File(s) ")
        for cop_file in sorted(missing_copyright_filtered):
            logger.info(f"  * {cop_file}")
            missing_copyright_list.append(os.path.join(path_to_find, cop_file))

        if license == "" and copyright == "":
            input_copyright = input_copyright_while_running()
        else:
            input_copyright = copyright

        if input_copyright != "":
            input_copyright = f"Copyright {input_copyright}"

            input_ok = check_input_copyright_format(input_copyright)
            if input_ok is False:
                return

            logger.warning(f"  * Your input Copyright : {input_copyright}")
            parsed_args = main_parser.parse_args(['addheader', '--copyright',
                                                  f'SPDX-FileCopyrightText: {input_copyright}',
                                                  '--exclude-year'] + missing_copyright_list)
            try:
                reuse_header(parsed_args, project)
            except Exception as ex:
                dump_error_msg(f"Error_call_run_in_copyright : {ex}")
    else:
        logger.info("\n# There is no missing copyright file\n")


def get_allfiles_list(path):
    try:
        for root, dirs, files in os.walk(path):
            for dir in dirs:
                if dir.startswith(EXCLUDE_PREFIX):
                    dirs.remove(dir)
                    continue
            for file in files:
                file_abs_path = os.path.join(root, file)
                file_rel_path = os.path.relpath(file_abs_path, path)
                yield file_rel_path
    except Exception as ex:
        dump_error_msg(f"Error - get all files list : {ex}")


def save_result_log():
    try:
        _str_final_result_log = safe_dump(_result_log, allow_unicode=True, sort_keys=True)
        logger.info(_str_final_result_log)
    except Exception as ex:
        logger.warning(f"Failed to print add result log. : {ex}")


def copy_to_root(path_to_find, input_license):
    lic_file = f"{input_license}.txt"
    try:
        source = os.path.join(path_to_find, 'LICENSES', f'{lic_file}')
        destination = os.path.join(path_to_find, 'LICENSE')
        shutil.copyfile(source, destination)
    except Exception as ex:
        dump_error_msg(f"Error - Can't copy license file: {ex}")


def lge_lic_download(path_to_find, input_license):
    success = False

    input_license_url = input_license.replace(' ', '_').replace('/', '_').replace('LicenseRef-', '').replace('-', '_')
    lic_url = OPENSOURCE_LGE_COM_URL_PREFIX + input_license_url + ".html"

    try:
        html = urllib.request.urlopen(lic_url)
        source = html.read()
        html.close()
    except urllib.error.URLError:
        logger.error("Invalid URL address")
    except ValueError as val_err:
        logger.error(f"Invalid Value : {val_err}")
    except Exception as ex:
        logger.error(f"Error to open url - {lic_url} : {ex}")

    soup = BeautifulSoup(source, 'html.parser')
    try:
        lic_text = soup.find("p", "bdTop")
        Path(os.path.join(os.getcwd(), path_to_find, 'LICENSES')).mkdir(parents=True, exist_ok=True)
        lic_file_path = os.path.join(path_to_find, 'LICENSES', f'{input_license}.txt')

        with open(lic_file_path, 'w', encoding='utf-8') as f:
            f.write(lic_text.get_text(separator='\n'))
        if os.path.isfile(lic_file_path):
            logger.info(f"Successfully downloaded {lic_file_path}")
            success = True
    except Exception as ex:
        logger.error(f"Error to download license from LGE : {ex}")
    return success


def present_license_file(path_to_find, lic):
    present = False
    lic_file_path = os.path.join(os.getcwd(), path_to_find, 'LICENSES')
    file_name = f"{lic}.txt"
    if file_name in os.listdir(lic_file_path):
        present = True
    return present


def find_representative_license(path_to_find, input_license):
    files = []
    found_file = []
    found_license_file = False
    main_parser = reuse_arg_parser()
    prj = Project(path_to_find)
    reuse_return_code = 0
    success_from_lge = False
    present_lic = False

    all_items = os.listdir(path_to_find)
    for item in all_items:
        if os.path.isfile(item):
            files.append(os.path.basename(item))

    for file in files:
        file_lower_case = file.lower()
        if file_lower_case in LICENSE_INCLUDE_FILES or file_lower_case.startswith("license") or file_lower_case.startswith("notice"):
            found_file.append(file)
            found_license_file = True

    input_license = check_input_license_format(input_license)
    logger.info(f" - Input License : {input_license}")

    parsed_args = main_parser.parse_args(['download', f"{input_license}"])

    try:
        # 0: successfully downloaded, 1: failed to download
        reuse_return_code = reuse_download(parsed_args, prj)
        # Check if the license text file is present
        present_lic = present_license_file(path_to_find, input_license)

        if reuse_return_code == 1 and not present_lic:
            # True : successfully downloaded from LGE
            success_from_lge = lge_lic_download(path_to_find, input_license)

        if reuse_return_code == 0 or success_from_lge:
            if found_license_file:
                logger.info(f"# Found representative license file : {found_file}\n")
            else:
                logger.warning(f"# Created Representative License File : {input_license}.txt\n")
                copy_to_root(path_to_find, input_license)
    except Exception as ex:
        dump_error_msg(f"Error - download representative license text: {ex}")


def is_exclude_dir(dir_path):
    if dir_path != "":
        dir_path = dir_path.lower()
        dir_path = dir_path if dir_path.endswith(
            os.path.sep) else dir_path + os.path.sep
        dir_path = dir_path if dir_path.startswith(
            os.path.sep) else os.path.sep + dir_path
        return any(dir_name in dir_path for dir_name in EXCLUDE_DIR)
    return


def download_oss_info_license(base_path, input_license=""):
    license_list = []
    converted_lic_list = []
    oss_yaml_files = []
    main_parser = reuse_arg_parser()
    prj = Project(base_path)

    oss_yaml_files = find_sbom_yaml_files(base_path)

    if input_license != "":
        license_list.append(input_license)

    if oss_yaml_files is None or len(oss_yaml_files) == 0:
        logger.info("\n # There is no OSS package Info file in this path\n")
        return
    else:
        logger.info(f"\n # There is OSS Package Info file(s) : {oss_yaml_files}\n")

    for oss_pkg_file in oss_yaml_files:
        _, license_list, _ = parsing_yml(oss_pkg_file, base_path)

    for lic in license_list:
        converted_lic_list.append(check_input_license_format(lic))

    if license_list is not None and len(license_list) > 0:
        parsed_args = main_parser.parse_args(['download'] + converted_lic_list)

        try:
            reuse_download(parsed_args, prj)
        except Exception as ex:
            dump_error_msg(f"Error - download license text in OSS-pkg-info.yml : {ex}")
    else:
        logger.info(" # There is no license in the path \n")


def add_content(target_path="", input_license="", input_copyright="", output_path="", need_log_file=True):
    global _result_log, spdx_licenses
    _check_only_file_mode = False
    file_to_check_list = []

    path_to_find, file_to_check_list, _check_only_file_mode = get_path_to_find(target_path, _check_only_file_mode)

    _, _, output_path, _, _ = check_output_format(output_path)
    if output_path == "":
        output_path = os.getcwd()
    else:
        output_path = os.path.abspath(output_path)

    now = datetime.now().strftime('%y%m%d_%H%M')
    logger, _result_log = init_log(os.path.join(output_path, f"fosslight_log_pre_{now}.txt"),
                                   need_log_file, logging.INFO, logging.DEBUG, PKG_NAME, path_to_find)

    if not os.path.isdir(path_to_find):
        logger.error(f"Check the path to find : {path_to_find}")
        sys.exit(1)

    # Get SPDX License List
    try:
        success, error_msg, licenses = get_spdx_licenses_json()
        if success is False:
            dump_error_msg(f"Error to get SPDX Licesens : {error_msg}")

        licenseInfo = licenses.get("licenses")
        for info in licenseInfo:
            shortID = info.get("licenseId")
            isDeprecated = info.get("isDeprecatedLicenseId")
            if isDeprecated is False:
                spdx_licenses.append(shortID)
    except Exception as ex:
        dump_error_msg(f"Error access to get_spdx_licenses_json : {ex}")

    # File Only mode (-f option)
    if _check_only_file_mode:
        main_parser = reuse_arg_parser()
        missing_license_list, missing_copyright_list, project = precheck_for_files(path_to_find, file_to_check_list)

        if input_license == "" and input_copyright == "":
            input_copyright = input_copyright_while_running()
            input_license = input_license_while_running()

        if missing_license_list is not None and len(missing_license_list) > 0:
            if input_license != "":
                converted_license = check_input_license_format(input_license)
                logger.warning(f"  * Your input license : {converted_license}")
                parsed_args = main_parser.parse_args(['addheader', '--license', f"{converted_license}"] + missing_license_list)
                try:
                    reuse_header(parsed_args, project)
                except Exception as ex:
                    dump_error_msg(f"Error_call_run_in_license_file_only : {ex}")
        else:
            logger.info("# There is no missing license file")

        if missing_copyright_list is not None and len(missing_copyright_list) > 0:
            if input_copyright != "":
                input_copyright = f"Copyright {input_copyright}"

                input_ok = check_input_copyright_format(input_copyright)
                if input_ok is False:
                    return

                logger.warning(f"  * Your input Copyright : {input_copyright}")
                parsed_args = main_parser.parse_args(['addheader', '--copyright',
                                                      f"SPDX-FileCopyrightText: {input_copyright}",
                                                      '--exclude-year'] + missing_copyright_list)
            try:
                reuse_header(parsed_args, project)
            except Exception as ex:
                dump_error_msg(f"Error_call_run_in_copyright_file_only : {ex}")
        else:
            logger.info("# There is no missing copyright file\n")
    # Path mode (-p option)
    else:
        # Download license text file of OSS-pkg-info.yaml
        download_oss_info_license(path_to_find, input_license)

        # Get missing license / copyright file list
        missing_license, missing_copyright, _, project, prj_report = precheck_for_project(path_to_find)

        # Get total files except excluded file
        total_files_excluded = get_total_file_list(path_to_find, prj_report, DEFAULT_EXCLUDE_EXTENSION_FILES)
        skip_files = sorted(set(total_files_excluded) - set(missing_license) - set(missing_copyright))
        logger.info(f"\n# File list that have both license and copyright : {len(skip_files)} / {len(total_files_excluded)}")

        # Filter by file extension
        missing_license = [file for file in missing_license if os.path.splitext(file)[1].lower() in EXTENSION_COMMENT_STYLE_MAP_LOWERCASE]
        missing_copyright = [file for file in missing_copyright if os.path.splitext(file)[1].lower() in EXTENSION_COMMENT_STYLE_MAP_LOWERCASE]

        # Check license and copyright of each file
        precheck_for_files(path_to_find, skip_files)

        # Set missing license and copyright
        set_missing_license_copyright(missing_license,
                                      missing_copyright,
                                      project,
                                      path_to_find,
                                      input_license,
                                      input_copyright)

    # Find and create representative license file
    if input_license != "" and len(missing_license) > 0:
        find_representative_license(path_to_find, input_license)

    save_result_log()
