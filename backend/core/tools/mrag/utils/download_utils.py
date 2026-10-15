import shutil

import requests

from .logger_utils import logger
from .oss_utils import resolve_local_url


def download_file(
        url: str,
        filename: str
):
    """
    通过url下载文件到指定本地路径

    :param url:
    :param filename:
    :return:
    """
    logger.info(f"Downloading {url} -> {filename}")
    local_path = resolve_local_url(url)
    if local_path:
        shutil.copyfile(local_path, filename)
        logger.info(f"Copied local storage file {filename}")
        return
    response = requests.get(url, stream=True, verify=False, timeout=300)
    if response.status_code == 200:
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        logger.info(f"Downloaded {filename}")
    else:
        logger.warning(f"Failed to download {filename}: HTTP {response.status_code}")
