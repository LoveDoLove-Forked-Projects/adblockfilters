import os
import asyncio
from concurrent.futures import ThreadPoolExecutor,as_completed

import httpx
import IPy
from tld import get_tld
from loguru import logger
from dns.asyncresolver import Resolver as DNSResolver
from dns.rdatatype import RdataType as DNSRdataType

class BlackList(object):
    def __init__(self):
        self.__ChinalistFile = os.getcwd() + "/rules/china.txt"
        self.__blacklistFile = os.getcwd() + "/rules/black.txt"
        self.__domainlistFile = os.getcwd() + "/rules/adblockdns.backup"
        self.__domainlistFile_CN = os.getcwd() + "/rules/direct-list.txt"
        self.__domainlistUrl_CN = "https://raw.githubusercontent.com/Loyalsoldier/v2ray-rules-dat/refs/heads/release/direct-list.txt"
        self.__iplistFile_CN = os.getcwd() + "/rules/CN-ip-cidr.txt"
        self.__iplistUrl_CN = "https://raw.githubusercontent.com/Hackl0us/GeoIP2-CN/refs/heads/release/CN-ip-cidr.txt"
        self.__maxTask = 500

    def __getDomainList(self):
        logger.info("resolve adblock dns backup...")
        domainList = []
        try:
            if os.path.exists(self.__domainlistFile):
                with open(self.__domainlistFile, 'r') as f:
                    tmp = f.readlines()
                    domainList = list(map(lambda x: x.replace("\n", ""), tmp))
        except Exception as e:
            logger.error("%s"%(e))
        finally:
            logger.info("adblock dns backup: %d"%(len(domainList)))
            return domainList
        
    def __getDomainSet_CN(self):
        logger.info("resolve China domain list...")
        domainSet = set()
        try:
            if os.path.exists(self.__domainlistFile_CN):
                os.remove(self.__domainlistFile_CN)
            
            with httpx.Client() as client:
                response = client.get(self.__domainlistUrl_CN)
                response.raise_for_status()
                with open(self.__domainlistFile_CN,'wb') as f:
                    f.write(response.content)
            
            if os.path.exists(self.__domainlistFile_CN):
                with open(self.__domainlistFile_CN, 'r') as f:
                    tmp = f.readlines()
                    domainSet = set(map(lambda x: x.replace("\n", ""), tmp))
        except Exception as e:
            logger.error("%s"%(e))
        finally:
            logger.info("China domain list: %d"%(len(domainSet)))
            return domainSet
        
    def __getIPDict_CN(self):
        logger.info("resolve China IP list...")
        IPDict = dict()
        try:
            if os.path.exists(self.__iplistFile_CN):
                os.remove(self.__iplistFile_CN)
            
            with httpx.Client() as client:
                response = client.get(self.__iplistUrl_CN)
                response.raise_for_status()
                with open(self.__iplistFile_CN,'wb') as f:
                    f.write(response.content)
            
            if os.path.exists(self.__iplistFile_CN):
                with open(self.__iplistFile_CN, 'r') as f:
                    for line in f.readlines():
                        row = line.replace("\n", "").split("/")
                        ip, offset = row[0], int(row[1])
                        IPDict[IPy.parseAddress(ip)[0]] = offset
        except Exception as e:
            logger.error("%s"%(e))
        finally:
            logger.info("China IP list: %d"%(len(IPDict)))
            return IPDict
    
    async def __resolve(self, dnsresolver, domain):
        ipList = []
        try:
            query_object = await dnsresolver.resolve(qname=domain, rdtype="A")
            query_item = None
            for item in query_object.response.answer:
                if item.rdtype == DNSRdataType.A:
                    query_item = item
                    break
            if query_item is None:
                raise Exception("not A type")
            for item in query_item:
                ip = '{}'.format(item)
                if ip != "0.0.0.0":
                    ipList.append(ip)
        except Exception as e:
            logger.error('"%s": %s' % (domain, e if e else "Resolver failed"))
        finally:
            return ipList

    async def __pingx(self, dnsresolver, domain, semaphore):
        async with semaphore: # 限制并发数，超过系统限制后会报错Too many open files
            host = domain
            port = None
            ipList = []
            if domain.rfind(":") > 0: # 兼容 host:port格式
                offset = domain.rfind(":")
                host = domain[ : offset]
                port = int(domain[offset + 1 : ])
            if port:
                try:
                    _, writer = await asyncio.open_connection(host, port)
                    writer.close()
                    await writer.wait_closed()
                    ipList.append(host)
                except Exception as e:
                    logger.error('"%s": %s' % (domain, e if e else "Connect failed"))
            else:
                count = 3
                while len(ipList) < 1 and count > 0:
                    ipList = await self.__resolve(dnsresolver, host)
                    count -= 1

            logger.info("%s: %s" % (domain, ipList))
            return domain, ipList

    def __generateBlackList(self, blackList):
        logger.info("generate black list...")
        try:
            if os.path.exists(self.__blacklistFile):
                os.remove(self.__blacklistFile)
            
            with open(self.__blacklistFile, "w") as f:
                for domain in blackList:
                    f.write("%s\n"%(domain))
            logger.info("block domain: %d"%(len(blackList)))
        except Exception as e:
            logger.error("%s"%(e))
    
    def __generateChinaList(self, ChinaList):
        logger.info("generate China list...")
        try:
            if os.path.exists(self.__ChinalistFile):
                os.remove(self.__ChinalistFile)
            
            with open(self.__ChinalistFile, "w") as f:
                for domain in ChinaList:
                    f.write("%s\n"%(domain))
            logger.info("China domain: %d"%(len(ChinaList)))
        except Exception as e:
            logger.error("%s"%(e))

    def __testDomain(self, domainList, nameservers, port=53):
        logger.info("resolve domain...")
        # 异步检测
        dnsresolver = DNSResolver()
        dnsresolver.nameservers = nameservers
        dnsresolver.port = port
        # 启动异步循环
        loop = asyncio.get_event_loop()
        semaphore = asyncio.Semaphore(self.__maxTask) # 限制并发量为500
        # 添加异步任务
        taskList = []
        for domain in domainList:
            task = asyncio.ensure_future(self.__pingx(dnsresolver, domain, semaphore))
            taskList.append(task)
        # 等待异步任务结束
        loop.run_until_complete(asyncio.wait(taskList))
        # 获取异步任务结果
        domainDict = {}
        for task in taskList:
            domain, ipList = task.result()
            domainDict[domain] = ipList

        logger.info("resolve domain: %d"%(len(domainDict)))
        return domainDict

    def __isChinaDomain(self, domain, ipList, domainSet_CN, IPDict_CN):
        isChinaDomain = False
        try:
            while True:
                if domain[-3:] == ".cn":
                    isChinaDomain = True
                    break

                res = get_tld(domain, fix_protocol=True, as_object=True)
                if res.fld in domainSet_CN:
                    isChinaDomain = True
                    break

                for ip in ipList:
                    ip = IPy.parseAddress(ip)[0]
                    for k, v in IPDict_CN.items():
                        if (ip ^ k) >> (32 - v)  == 0:
                            isChinaDomain = True
                            break
                    if isChinaDomain:
                        break
                
                break
        except Exception as e: 
            logger.error('"%s": not domain'%(domain))
        finally:
            return domain,isChinaDomain

    def generate(self):
        try:
            domainList = self.__getDomainList()
            if len(domainList) < 1:
                return
            #domainList = domainList[:1000] # for test
            
            domainDict = self.__testDomain(domainList, ["127.0.0.1"], 5053) # 使用本地 smartdns 进行域名解析，配置3组国内、3组国际域名解析服务器，提高识别效率
            #domainDict = self.__testDomain(domainList, ["1.12.12.12"], 53) # for test

            domainSet_CN = self.__getDomainSet_CN()
            IPDict_CN = self.__getIPDict_CN()
            blackList = []
            if len(domainSet_CN) > 100 and len(IPDict_CN) > 100:
                thread_pool = ThreadPoolExecutor(max_workers=os.cpu_count() if os.cpu_count() > 4 else 4)
                taskList = []
                for domain in domainList:
                    if len(domainDict[domain]):
                        taskList.append(thread_pool.submit(self.__isChinaDomain, domain, domainDict[domain], domainSet_CN, IPDict_CN))
                    else:
                        blackList.append(domain)
                # 获取解析结果
                ChinaSet_tmp = set()
                for future in as_completed(taskList):
                    domain,isChinaDomain = future.result()
                    if isChinaDomain:
                        ChinaSet_tmp.add(domain)
                # 生成China域名列表
                ChinaList = []
                for domain in domainList:
                    if domain in ChinaSet_tmp:
                        ChinaList.append(domain)
                if len(ChinaList):
                    self.__generateChinaList(ChinaList)
            else:
                for domain in domainList:
                    if domainDict[domain] is None:
                        blackList.append(domain)

            # 生成黑名单
            if len(blackList):
                self.__generateBlackList(blackList)
        except Exception as e:
            logger.error("%s"%(e))

if __name__ == "__main__":
    #logger.add(os.getcwd() + "/blacklist.log")
    blackList = BlackList()
    blackList.generate()