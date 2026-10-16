#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redmi AX1800/CR660X 路由器(MiWiFi 官方固件)一键开启 SSH root
测试固件版本：1.0.93

用法:
    python miwifi_ssh_unlock.py
    然后按提示输入 路由器IP 和 管理密码, 一键完成。

    也可免交互: python miwifi_ssh_unlock.py <IP> <管理密码>

说明:
  1. 网页登录后台获取 stok (登录算法: SHA1(nonce + SHA1(密码 + key)), key/nonce
     规则动态从登录页解析, 不同固件版本可自适应)。
  2. 通过命令注入接口 /cgi-bin/luci/;stok=<stok>/api/xqdatacenter/request
     (payload api=629) 分块执行开启命令, 每块带标记回显校验。
  3. 开启 dropbear SSH, root 密码默认设为管理密码。
  4. 探测 22 端口 banner 做真实验证。
  5. 自动刷新本机 known_hosts 里该 IP 的主机指纹(每次重开 SSH 都会生成新密钥,
     不刷新的话下次连接会报 HOST IDENTIFICATION HAS CHANGED)。

仅需 Python 3.6+ 标准库, 无需安装任何依赖。请仅在自己拥有的路由器上使用。
"""

import getpass
import hashlib
import http.cookiejar
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 输出统一为 UTF-8 且不因个别无法编码的字符而崩溃(如 GBK 控制台)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

DEFAULT_KEY = 'a2ffa5c9be07488bbb04a3a47d3c5f6a'
DEFAULT_DEVICE_ID = '00:e0:00:00:00:00'
SAFE_PWD = re.compile(r'[A-Za-z0-9._-]{1,40}')


class Fatal(Exception):
    pass


def p_ok(msg):
    print('[OK] ' + msg, flush=True)


def p_fail(msg):
    print('[X ] ' + msg, flush=True)


def p_info(msg):
    print('... ' + msg, flush=True)


def ask(prompt, secret=False):
    if secret and sys.stdin.isatty():
        try:
            return getpass.getpass(prompt)
        except Exception:
            pass
    return input(prompt)


class MiWifi(object):
    def __init__(self, ip):
        self.base = 'http://' + ip
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
        self.stok = None
        self.key = DEFAULT_KEY
        self.device_id = DEFAULT_DEVICE_ID

    def _req(self, path, data=None, timeout=12):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer': self.base + '/cgi-bin/luci/web',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': '*/*',
        }
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode('utf-8')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(self.base + path, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            try:
                snippet = e.read().decode('utf-8', 'replace')[:160]
            except Exception:
                snippet = ''
            raise Fatal('HTTP %s: %s' % (e.code, snippet))

    # ---------- 1. 登录 ----------
    def load_login_page(self):
        html = self._req('/cgi-bin/luci/web')
        if 'rtloginform' not in html and 'router_password' not in html:
            raise Fatal('打不开登录页, 请确认 IP 正确且路由器可达')
        m = re.search(r"Encrypt\s*=\s*\{[\s\S]{0,300}?key\s*:\s*'([0-9a-fA-F]+)'", html)
        if m:
            self.key = m.group(1)
        m = re.search(r"nonceCreat\s*:\s*function[\s\S]{0,200}?deviceId\s*=\s*'([^']+)'", html)
        if m:
            self.device_id = m.group(1)

    def login(self, password):
        nonce = '0_%s_%d_%d' % (self.device_id, int(time.time()), random.randint(0, 9999))
        inner = hashlib.sha1((password + self.key).encode('utf-8')).hexdigest()
        hashed = hashlib.sha1((nonce + inner).encode('utf-8')).hexdigest()
        rsp = self._req('/cgi-bin/luci/api/xqsystem/login', {
            'username': 'admin',
            'password': hashed,
            'logtype': 2,
            'nonce': nonce,
        })
        try:
            j = json.loads(rsp)
        except ValueError:
            raise Fatal('登录接口返回异常: ' + rsp[:160])
        code = j.get('code')
        if code == 403:
            raise Fatal('密码错误次数过多已被临时锁定, 请稍后再试')
        if code != 0:
            raise Fatal('登录失败 code=%s msg=%s (管理密码是否正确?)'
                        % (code, j.get('msg', '')))
        m = (re.search(r'stok=([0-9a-fA-F]+)', str(j.get('url') or ''))
             or re.search(r'stok=([0-9a-fA-F]+)', rsp))
        if not m:
            tok = str(j.get('token') or '')
            m = re.match(r'^([0-9a-fA-F]{16,})$', tok)
        if not m:
            raise Fatal('登录成功但没有取得 stok')
        self.stok = m.group(1)

    # ---------- 2. 注入 ----------
    def inject(self, appid):
        payload = json.dumps({'api': 629, 'appid': appid}, separators=(',', ':'))
        path = '/cgi-bin/luci/;stok=%s/api/xqdatacenter/request?payload=%s' % (
            self.stok, urllib.parse.quote(payload, safe=''))
        return self._req(path).strip()

    def run_chunks(self, chunks):
        all_ok = True
        for desc, appid, marker in chunks:
            resp, ok = '', False
            for _ in range(2):
                resp = self.inject(appid)
                if 'Invalid token' in resp:
                    raise Fatal('stok 已失效, 请重新运行本脚本')
                if 'No page is registered' in resp:
                    raise Fatal('该固件没有 xqdatacenter/request 注入通道, 本脚本不适用')
                if marker in resp:
                    ok = True
                    break
                time.sleep(1)
            if ok:
                p_ok(desc)
            else:
                snippet = resp[:120].replace('\r', ' ').replace('\n', ' ')
                p_fail('%s | 返回: %s' % (desc, snippet))
                all_ok = False
        return all_ok


def wait_ssh_banner(ip, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            s = socket.create_connection((ip, 22), timeout=3)
            try:
                s.settimeout(3)
                data = s.recv(128) or b''
            finally:
                s.close()
            if data.startswith(b'SSH-'):
                # 只取 banner 首行(其后是二进制握手数据), 仅保留可打印 ASCII
                line = data.split(b'\r\n')[0].split(b'\n')[0]
                banner = ''.join(chr(b) for b in line if 32 <= b < 127)
                if banner.startswith('SSH-'):
                    return banner.strip()
        except OSError:
            pass
        time.sleep(1.5)
    return None


def refresh_known_hosts(ip):
    """移除该 IP 的旧主机指纹并登记本次新密钥, 免去手工 ssh-keygen -R"""
    kh = os.path.expanduser(os.path.join('~', '.ssh', 'known_hosts'))
    removed = False
    registered = False

    def run(args):
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True)
            out, _ = p.communicate(timeout=20)
            return p.returncode, out or ''
        except Exception:
            return -1, ''

    if shutil.which('ssh-keygen') and os.path.exists(kh):
        rc, _ = run(['ssh-keygen', '-R', ip])
        removed = (rc == 0)

    if shutil.which('ssh-keyscan'):
        rc, out = run(['ssh-keyscan', '-T', '5', '-t', 'rsa', ip])
        keys = [l for l in out.splitlines()
                if l.strip() and not l.startswith('#') and ' ssh-rsa ' in l]
        if keys:
            try:
                os.makedirs(os.path.dirname(kh), exist_ok=True)
                with open(kh, 'a', encoding='utf-8', errors='replace') as f:
                    f.write('\n'.join(keys) + '\n')
                registered = True
            except Exception:
                pass

    if registered:
        p_ok('本机 known_hosts 主机指纹已更新为本次新密钥, 可直接连接')
    elif removed:
        p_info('已清除旧指纹, 首次连接时确认 yes 即可')
    else:
        p_info('如连接报 HOST IDENTIFICATION HAS CHANGED, 手动执行: ssh-keygen -R %s' % ip)


def build_chunks(ssh_pwd):
    return [
        ('写入 nvram 标志 ssh_en/uart_en/boot_wait 并 commit',
         '; echo C1; nvram set ssh_en=1; nvram set uart_en=1; nvram set boot_wait=on; nvram commit; #', 'C1'),
        ('备份 dropbear 启动脚本 -> dropbear_backup',
         '; echo C2; cp /etc/init.d/dropbear /etc/init.d/dropbear_backup; #', 'C2'),
        ('移除 dropbear 启动脚本里的 SSH 限制',
         "; echo C3; sed -i '/flg_ssh.*release/ { :a; N; /fi/! ba };/return 0/d' /etc/init.d/dropbear; #", 'C3'),
        ('设置 dropbear channel=debug',
         "; echo C4; sed -i 's/channel=.*/channel=debug/g' /etc/init.d/dropbear; #", 'C4'),
        ('设置 root 密码',
         '; echo C5; ({ echo %s; echo %s; } | passwd root); #' % (ssh_pwd, ssh_pwd), 'C5'),
        ('注册开机自启并立即启动 dropbear',
         '; echo C6; /etc/init.d/dropbear enable; /etc/init.d/dropbear start; '
         'echo /etc/init.d/dropbear >> /etc/sysupgrade.conf; #', 'C6'),
    ]


def main():
    if len(sys.argv) >= 3:
        ip, admin_pwd = sys.argv[1].strip(), sys.argv[2]
    else:
        print('=== 小米/Redmi 路由器一键开启 SSH (root) ===')
        ip = input('路由器 IP [192.168.31.1]: ').strip() or '192.168.31.1'
        admin_pwd = ask('路由器管理密码: ', secret=True)
    if not admin_pwd:
        raise Fatal('密码不能为空')

    if SAFE_PWD.match(admin_pwd):
        ssh_pwd = admin_pwd
        p_info('SSH root 密码将与管理密码相同')
    else:
        ssh_pwd = ask('管理密码含特殊字符, 请另设 SSH root 密码(字母/数字/._- 最多40位): ',
                      secret=True)
        if not ssh_pwd or not SAFE_PWD.match(ssh_pwd):
            raise Fatal('SSH 密码只允许字母、数字和 . _ -, 且不超过 40 位')

    r = MiWifi(ip)

    p_info('读取登录页 ...')
    r.load_login_page()

    p_info('登录 %s ...' % ip)
    r.login(admin_pwd)
    p_ok('登录成功, stok=%s...' % r.stok[:8])

    p_info('执行开启命令 ...')
    chunks_ok = r.run_chunks(build_chunks(ssh_pwd))

    resp = r.inject('; nvram get ssh_en; #')
    if resp.strip() == '1':
        p_ok('nvram ssh_en=1 已生效')
    else:
        p_fail('nvram ssh_en 校验返回: %s' % resp[:80])

    p_info('等待 22 端口 SSH banner ...')
    banner = wait_ssh_banner(ip)
    if not banner:
        raise Fatal('22 端口未出现 SSH banner, 开启失败%s'
                    % (' (上面有步骤报错)' if not chunks_ok else ''))

    p_ok('SSH 已开启: %s' % banner)

    p_info('刷新本机 known_hosts 指纹 ...')
    refresh_known_hosts(ip)

    print('')
    print('连接方式:')
    print('  ssh -o HostKeyAlgorithms=+ssh-rsa root@%s' % ip)
    print('  用户名: root')
    print('  密码: %s' % ('与管理密码相同' if ssh_pwd == admin_pwd else '你设置的专用密码'))
    print('')
    print('提示: 该固件重启后可能丢失以上修改, 再运行一次本脚本即可重新开启。')


if __name__ == '__main__':
    try:
        main()
    except Fatal as e:
        p_fail(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print('\n已取消')
        sys.exit(130)
    except urllib.error.URLError as e:
        p_fail('网络错误: %s' % e)
        sys.exit(1)
    except OSError as e:
        p_fail('网络错误: %s' % e)
        sys.exit(1)
