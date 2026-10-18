# 环境基线记录

- 采集时间：2026-09-14 18:49:22 CST
- 采集主机：iZ2zeiuczdbdkgyfuir2j8Z（iZ2zeiuczdbdkgyfuir2j8Z）
- 采集人：root
- 依据：《公网云服务器验证环境准备》第二章（表1 及交付前确认清单）

## 1. 操作系统与内核

```console
$ uname -a
Linux iZ2zeiuczdbdkgyfuir2j8Z 3.10.0-514.26.2.nfs.8.x86_64 #1 SMP Tue Jan 29 11:08:35 CST 2019 x86_64 x86_64 x86_64 GNU/Linux
```

```console
$ uname -m
x86_64
```

```console
$ cat /etc/os-release
NAME="NFS Server"
VERSION="3.1 (RTM4-U1)"
ID="NFS"
ID_LIKE="NFS"
VERSION_ID="3.1"
PRETTY_NAME="NFS Server 3.1 (RTM4-U1)"
ANSI_COLOR="0;31"
HOME_URL="http://www.nfschina.com"

```

```console
$ rpm -q kernel
kernel-3.10.0-514.26.2.nfs.8.x86_64
```

## 2. CPU（验收关注：型号 / 物理核-逻辑核 / 指令集）

```console
$ lscpu
Architecture:          x86_64
CPU op-mode(s):        32-bit, 64-bit
Byte Order:            Little Endian
CPU(s):                8
On-line CPU(s) list:   0-7
Thread(s) per core:    2
Core(s) per socket:    4
座：                 1
NUMA 节点：         1
厂商 ID：           GenuineIntel
CPU 系列：          6
型号：              85
型号名称：        Intel(R) Xeon(R) Platinum
步进：              4
CPU MHz：             2499.998
BogoMIPS：            4999.99
超管理器厂商：  KVM
虚拟化类型：     完全
L1d 缓存：          32K
L1i 缓存：          32K
L2 缓存：           1024K
L3 缓存：           33792K
NUMA 节点0 CPU：    0-7
```

```console
$ 指令集摘要（SSE4/AVX 系）
```

## 3. 内存与磁盘（验收关注：总量 / /opt 可用空间）

```console
$ free -h
              total        used        free      shared  buff/cache   available
Mem:            30G         13G        755M         17M         16G         16G
Swap:          6.0G         33M        6.0G
```

```console
$ df -h
文件系统             容量  已用  可用 已用% 挂载点
/dev/mapper/n3-root   92G   25G   68G   27% /
devtmpfs              16G     0   16G    0% /dev
tmpfs                 16G   96K   16G    1% /dev/shm
tmpfs                 16G   18M   16G    1% /run
tmpfs                 16G     0   16G    0% /sys/fs/cgroup
/dev/vda1            2.0G  542M  1.3G   30% /boot
tmpfs                3.1G   16K  3.1G    1% /run/user/1000
overlay               92G   25G   68G   27% /var/lib/docker/overlay2/9401fc508497ebac7141f362e80747f8309c61bb2e6531fe66e10c30ef5a50b7/merged
overlay               92G   25G   68G   27% /var/lib/docker/overlay2/a1aaf0a2e14dcfdf57367dea6e7473493b3a2297e72171a2a7c2310a2be7b016/merged
overlay               92G   25G   68G   27% /var/lib/docker/overlay2/c37872d0afbb0c784e1ea9678412b8c27c2d3c6d496aa334d02fd13e72e1bffc/merged
overlay               92G   25G   68G   27% /var/lib/docker/overlay2/69d1f92640d97114efe9c4a69ebcc5d57e540bc436fc85faca010a872c48663f/merged
overlay               92G   25G   68G   27% /var/lib/docker/overlay2/9aa5de1a5df2e996e333888378c8655c48efafcda7bb622125b2bfec4a135e19/merged
overlay               92G   25G   68G   27% /var/lib/docker/overlay2/c135994e8c30ecfc334ed4e6797c1c2b8ec5e9bef71d021d0fd502e2bca205ad/merged
tmpfs                3.1G     0  3.1G    0% /run/user/0
```

```console
$ /opt 可用空间
文件系统             容量  已用  可用 已用% 挂载点
/dev/mapper/n3-root   92G   25G   68G   27% /
```

## 4. 关键系统运行库（glibc / gcc / libstdc++ / OpenSSL）

```console
$ ldd --version
ldd (GNU libc) 2.17
Copyright (C) 2012 Free Software Foundation, Inc.
This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
由 Roland McGrath 和 Ulrich Drepper 编写。
```

```console
$ gcc --version
gcc (GCC) 4.8.5 20150623 (Red Hat 4.8.5-4)
Copyright © 2015 Free Software Foundation, Inc.
本程序是自由软件；请参看源代码的版权声明。本软件没有任何担保；
包括没有适销性和某一专用目的下的适用性担保。
```

```console
$ g++ --version
g++ (GCC) 4.8.5 20150623 (Red Hat 4.8.5-4)
Copyright © 2015 Free Software Foundation, Inc.
本程序是自由软件；请参看源代码的版权声明。本软件没有任何担保；
包括没有适销性和某一专用目的下的适用性担保。
```

```console
$ rpm -q glibc libstdc++ gcc gcc-c++ openssl
glibc-2.17-106.nfs.8.x86_64
libstdc++-4.8.5-4.nfs.x86_64
gcc-4.8.5-4.nfs.x86_64
gcc-c++-4.8.5-4.nfs.x86_64
openssl-1.0.2k-1.nfs.x86_64
```

```console
$ openssl version
OpenSSL 1.0.2k-fips  26 Jan 2017
```

```console
# libstdc++ GLIBCXX 上限
GLIBCXX_3.4.18
GLIBCXX_3.4.19
GLIBCXX_DEBUG_MESSAGE_LENGTH
```

## 5. Python 3.11（验收项 2：解释器 / pip / venv）

```console
$ python3.11 --version
Python 3.11.14
```

```console
$ python3.11 -m pip --version
pip 24.0 from /usr/local/lib/python3.11/site-packages/pip (python 3.11)
```

```console
$ python3.11 -m venv --help >/dev/null && echo venv-ok
venv-ok
```

```console
$ 系统自带 python（仅记录，不作基线）
deploy/env-prep/collect_baseline.sh:行75: python3: 未找到命令
(无 python3)
```

## 6. 网络与访问（IP / SSH 端口 / 防火墙 / SELinux）

```console
$ hostname -I
172.16.233.57 192.168.122.1 172.17.0.1 172.18.0.1 
```

```console
$ SSH 端口
Port 22
```

```console
$ firewalld / SELinux
inactive
firewalld: inactive/absent
[91mFirewallD is not running[00m
Disabled
```

## 7. 容器套件现状（RAGFlow 栈，供差异记录）

```console
$ docker --version
Docker version 26.1.4, build 5650f9b
```

```console
$ docker ps（运行中容器）
NAMES                  IMAGE                                     STATUS                PORTS
docker-ragflow-cpu-1   infiniflow/ragflow:v0.27.1                Up 4 days             0.0.0.0:9380-9384->9380-9384/tcp, :::9380-9384->9380-9384/tcp, 0.0.0.0:8080->80/tcp, :::8080->80/tcp, 0.0.0.0:8443->443/tcp, :::8443->443/tcp
docker-mysql-1         mysql:8.0.40                              Up 4 days (healthy)   0.0.0.0:3306->3306/tcp, :::3306->3306/tcp, 33060/tcp
docker-minio-1         pgsty/silo:RELEASE.2026-08-06T00-00-00Z   Up 4 days (healthy)   0.0.0.0:9000-9001->9000-9001/tcp, :::9000-9001->9000-9001/tcp
docker-es01-1          elasticsearch:8.11.3                      Up 4 days (healthy)   9300/tcp, 0.0.0.0:1200->9200/tcp, :::1200->9200/tcp
mineru                 ragflow-mineru:latest                     Up 4 days             0.0.0.0:8001->8001/tcp, :::8001->8001/tcp
docker-redis-1         valkey/valkey:8                           Up 4 days (healthy)   0.0.0.0:6379->6379/tcp, :::6379->6379/tcp
```

---

> 云环境与最终生产环境的差异项（如有）请在下方登记（验证文档 §八）：
>
> | 差异项 | 云环境实际值 | 生产要求值 | 影响评估 |
> |---|---|---|---|
> | （待填） | | | |
