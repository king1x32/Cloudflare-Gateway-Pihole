import asyncio
import aiohttp

from loguru import logger
from src import cloudflare, convert 

class App:

    def __init__(
        self, adlist_name: str, adlist_urls: list[str], whitelist_urls: list[str]
    ):
        self.adlist_name = adlist_name
        self.adlist_urls = adlist_urls
        self.whitelist_urls = whitelist_urls
        self.name_prefix = f"[AdBlock-{adlist_name}]"

    async def run(self):

        # Download block and white content
        async with aiohttp.ClientSession() as session:
            block_content = "".join(
                await asyncio.gather(
                    *[
                        self.download_file(session, url)
                        for url in self.adlist_urls
                    ]
                )
            )
            white_content = "".join(
                await asyncio.gather(
                    *[
                        self.download_file(session, url)
                        for url in self.whitelist_urls
                    ]
                )
            )

        # Add dynamic_blacklist
        with open("./lists/dynamic_blacklist.txt", "r") as block_file:
            block_content += block_file.read()

        # Add dynamic_whitelist
        with open("./lists/dynamic_whitelist.txt", "r") as white_file:
            white_content += white_file.read()

        domains = convert.convert_to_domain_list(block_content, white_content)

        # check if number of domains exceeds the limit
        if len(domains) == 0:
            logging.warning(
                "No domains found in the adlist file. Exiting script.")
            return

        # check if the list is already in Cloudflare
        cf_lists = await cloudflare.get_lists(self.name_prefix)

        logger.info(f"Number of lists in Cloudflare: {len(cf_lists)}")

        # compare the lists size
        if len(domains) == sum([l["count"] for l in cf_lists]):
            logger.warning("Lists are the same size, checking policy")
            cf_policies = await cloudflare.get_firewall_policies(self.name_prefix)

            if len(cf_policies) == 0:
                logger.info("No firewall policy found, creating new policy")
                cf_policies = await cloudflare.create_gateway_policy(
                    f"{self.name_prefix} Block Ads", [
                        l["id"] for l in cf_lists]
                )
            else:
                logger.warning("Firewall policy already exists, exiting script")
                return

            return

        # Delete existing policy created by script
        policy_prefix = f"{self.name_prefix} Block Ads"
        deleted_policies = await cloudflare.delete_gateway_policy(policy_prefix)
        logger.info(f"Deleted {deleted_policies} gateway policies")

        # Delete old lists on Cloudflare
        delete_list_tasks = []
        for l in cf_lists:
            logger.info(f"Deleting list {l['name']} - ID:{l['id']} ")
            delete_list_tasks.append(cloudflare.delete_list(l["name"], l["id"]))
        await asyncio.gather(*delete_list_tasks)

        # Start creating new lists and firewall policy concurrently
        create_list_tasks = []
        for i, chunk in enumerate(self.chunk_list(domains, 1000)):
            list_name = f"{self.name_prefix} {i + 1}"
            logger.info(f"Creating list {list_name}")
            create_list_tasks.append(cloudflare.create_list(list_name, chunk))

        cf_lists = await asyncio.gather(*create_list_tasks)

        cf_policies = await cloudflare.get_firewall_policies(self.name_prefix)
        logger.info(f"Number of policies in Cloudflare: {len(cf_policies)}")

        # setup the gateway policy
        if len(cf_policies) == 0:
            logger.info("Creating firewall policy")
            cf_policies = await cloudflare.create_gateway_policy(
                policy_prefix, [l["id"] for l in cf_lists]
            )
        elif len(cf_policies) != 1:
            logger.error("More than one firewall policy found")
            raise Exception("More than one firewall policy found")
        else:
            logger.info("Updating firewall policy")
            await cloudflare.update_gateway_policy(
                f"{self.name_prefix} Block Ads",
                cf_policies[0]["id"],
                [l["id"] for l in cf_lists],
            )

        logger.info("Done")

    async def download_file(self, session: aiohttp.ClientSession, url: str):
        async with session.get(url) as response:
            text = await response.text("utf-8")
            logger.info(f"Downloaded file from {url} File size: {len(text)}")
            return text

    def chunk_list(self, _list: list[str], n: int):
        for i in range(0, len(_list), n):
            yield _list[i: i + n]

    async def delete(self):
        # Delete gateway policy
        policy_prefix = f"{self.name_prefix} Block Ads"
        deleted_policies = await cloudflare.delete_gateway_policy(policy_prefix)
        logger.info(f"Deleted {deleted_policies} gateway policies")

        # Delete lists
        cf_lists = await cloudflare.get_lists(self.name_prefix)
        delete_list_tasks = []
        for l in cf_lists:
            logger.info(f"Deleting list {l['name']} - ID:{l['id']} ")
            delete_list_tasks.append(cloudflare.delete_list(l["name"], l["id"]))
        await asyncio.gather(*delete_list_tasks)
        logger.info("Deletion completed")
