#!/usr/bin/env python3
import os
import subprocess
import time
import re
import threading
import json
from collections import defaultdict
import urllib.parse

import wx
import psutil
import requests

class BiliClient:
    def __init__(self, timeout=10):
        self.session = requests.Session()
        self.session.headers.update({
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
        })
        self.timeout = timeout
        self._redirect_cache = {}
        self._info_cache = {}
        self._playurl_cache = defaultdict(dict)

    def parse_b23(self, url):
        if not url.startswith('http'):
            url = 'http://' + url
            return self.parse_b23(url)
        if url in self._redirect_cache:
            return self._redirect_cache[url]
        r = self.session.get(url, stream=True, timeout=self.timeout)
        if r.status_code == 200:
            self._redirect_cache[url] = r.url
            return r.url
    
    def load_login(self):
        try:
            with open('cookies.json', 'rb') as f:
                data = json.load(f)

            cookies = { i["name"]: i["value"] for i in data["cookie_info"]["cookies"] }
            return cookies
        except Exception:
            return {}
    
    def get_api(self, url, **kwargs):
        r = self.session.get(url, timeout=self.timeout, **kwargs)
        if r.status_code == 200:
            if r.json()['code'] != 0:
                print(r.json())
            else:
                return r.json()['data']

    def get_video_info(self, vid):
        if vid in self._info_cache:
            return self._info_cache[vid]
        url = 'https://api.bilibili.com/x/web-interface/view'
        if vid.upper().startswith('BV'):
            params = { 'bvid': vid }
        else:
            if isinstance(vid, int):
                params = { 'aid': vid }
            else:
                assert vid.startswith('av'), 'unexpected vid format %s' % vid
                params = { 'aid': int(vid[2:]) }
        data = self.get_api(url, params=params)
        if not data: return
        self._info_cache[data['bvid']] = data
        self._info_cache[data['aid']] = data
        self._info_cache['av%d' % data['aid']] = data
        return data

    def get_playurl(self, vid, page):
        info = self.get_video_info(vid)
        if not info: return
        cookies = self.load_login()
        cache = self._playurl_cache[cookies.get('SESSDATA', None)]
        if len(info['pages']) > 1:
            page = page or 1
            p = info['pages'][int(page)-1]
            title = p['part']
        else:
            p = info['pages'][0]
            title = info['title']
        cid = p['cid']
        if cid in cache:
            return cache[cid]
        else:
            url = "https://api.bilibili.com/x/player/playurl"
            params = {
                'bvid': info['bvid'],
                'cid': cid,
                'qn': 120,
                'otype': 'json',
            }
            data = self.get_api(url, params=params, cookies=cookies)
            if not data: return
            durl = data['durl'][0]['url']
            q_desc = data['accept_description'][data['accept_quality'].index(data['quality'])]
            cache[cid] = title, data['quality'], q_desc, durl
            return cache[cid]

    def get_filename(self, url, headers={}):
        r = self.session.get(url, stream=True, timeout=self.timeout, headers=headers)
        filename = os.path.basename(r.url).split('?')[0]
        value = r.headers.get('Content-Disposition', '')
        m = re.search(r'filename="([^"]+)"', value)
        if m: filename = m[1]
        m = re.search(r"filename\*=[^']+'[^']*'([^;\s]+)", value)
        if m: filename = m[1]
        return urllib.parse.unquote(filename)

class URLText(wx.TextCtrl):
    def __init__(self, parent, info_text, quality_text, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_blur)
        self.focus = False
        self.durl = None
        self.filename = None
        self.headers = {}
        self.info_text = info_text
        self.quality_text = quality_text
        self.client = BiliClient()

    def on_focus(self, event):
        event.Skip()
        self.focus = True

    def on_blur(self, event):
        event.Skip()
        self.focus = False
        self.parse_url()

    def parse_url(self):
        threading.Thread(target=self._parse_url).start()

    bilibili_re = re.compile(r'(^|bilibili\.com/video/)(av\d+|[Bb][Vv][A-Za-z0-9]+)/?(\?(.*&)?p=(\d+))?')
    def _parse_url(self):
        self.durl = None
        self.filename = None
        self.headers = {}
        self.info_text.SetLabelText('')
        self.quality_text.SetLabelText('')

        url = self.GetValue()
        if 'b23.tv' in url:
            if self.client.parse_b23(url):
                if self.GetValue() != url or self.focus:
                    return
                url = self.client.parse_b23(url)
                self.SetValue(url)

        m = self.bilibili_re.search(url)
        if m:
            _, vid, _, _, page = m.groups()
            self.info_text.SetLabelText('%s\t%s' % (vid, page or 1))
            title, qn, q_desc, durl = self.client.get_playurl(vid, page)
            if self.GetValue() != url:
                return
            self.info_text.SetLabelText(title)
            self.quality_text.SetLabelText('%s (%s)' % (q_desc, qn))
            self.durl = durl
            self.headers['referer'] = 'https://www.bilibili.com/video/%s/' % vid

            filename = self.client.get_filename(self.durl)
            if self.GetValue() != url:
                return
            self.filename = filename
            self.quality_text.SetLabelText('%s (%s) %s' % (q_desc, qn, filename))
        elif url.startswith('http'):
            self.durl = url
            filename = self.client.get_filename(self.durl)
            if self.GetValue() != url:
                return
            self.filename = filename
            self.info_text.SetLabelText(filename)

class TimecodeText(wx.TextCtrl):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_blur)

    def on_blur(self, event):
        event.Skip()
        self.focus = False
        if self.GetValue() and not self.timecode:
            self.SetBackgroundColour( wx.Colour( 255, 128, 0 ) )
        else:
            self.SetBackgroundColour( None )

    @property
    def timecode(self):
        if not self.GetValue():
            return None
        try:
            seconds = 0
            parser = float if '.' in self.GetValue() else int
            for i in self.GetValue().split(':'):
                seconds *= 60
                seconds += parser(i)
            return seconds
        except Exception:
            return None


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(parent=None, title='Bilibili视频片段下载')
        panel = wx.Panel(self)
        v_sizer = wx.BoxSizer(wx.VERTICAL)
        
        _label = wx.StaticText(panel, label="BV号/av号/视频链接")
        self.info_text = wx.StaticText(panel, label="")
        self.quality_text = wx.StaticText(panel, label="")
        self.video_ctrl = URLText(panel, self.info_text, self.quality_text)
        
        v_sizer.Add(_label, proportion=0, flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND | wx.ALIGN_LEFT, border = 5)
        v_sizer.Add(self.video_ctrl, proportion=0, flag=wx.ALL | wx.EXPAND, border = 5)
        v_sizer.Add(self.info_text, proportion=0, flag=wx.LEFT | wx.RIGHT | wx.EXPAND | wx.ALIGN_LEFT, border = 5)
        v_sizer.Add(self.quality_text, proportion=0, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND | wx.ALIGN_LEFT, border = 5)

        ts_sizer = wx.GridSizer(rows=2, cols=2, vgap=0, hgap=10)
        ts_sizer.AddMany([
            (wx.StaticText(panel, label="开始时间"), 0, wx.EXPAND),
            (wx.StaticText(panel, label="结束时间"), 0, wx.EXPAND),
        ])
        self.start_ts = TimecodeText(panel)
        ts_sizer.Add(self.start_ts, proportion=0)
        self.end_ts = TimecodeText(panel)
        ts_sizer.Add(self.end_ts, proportion=0)
        v_sizer.Add(ts_sizer, proportion=0, flag=wx.ALL | wx.EXPAND, border = 5)

        self.login_btn = wx.Button(panel, label='用户登录')
        self.login_btn.Bind(wx.EVT_BUTTON, self.login)
        v_sizer.Add(self.login_btn, 0, wx.ALL | wx.ALIGN_LEFT, 10)
        self.start_btn = wx.Button(panel, label='开始下载')
        self.start_btn.Bind(wx.EVT_BUTTON, self.start)
        v_sizer.Add(self.start_btn, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_LEFT, 10)
        self.process_text = wx.StaticText(panel, label="")
        v_sizer.Add(self.process_text, proportion=0, flag=wx.ALL | wx.ALIGN_LEFT, border = 5)

        v_sizer.AddSpacer(10)

        panel.SetSizerAndFit(v_sizer)
        x, y = v_sizer.ComputeFittingWindowSize(self)
        self.SetSize((x+100, y))
        self.Show()

    def login(self, event):
        login_exe = os.path.join(os.path.dirname(__file__), 'biliup.exe')
        for proc in psutil.process_iter(['pid', 'exe']):
            if proc.info['exe'] == login_exe:
                proc.terminate()
        os.system('start biliup.exe login')
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            if proc.info['name'] == 'biliup.exe':
                proc.wait()
        self.video_ctrl.parse_url()

    def start(self, event):
        if not self.video_ctrl.durl or not self.video_ctrl.filename:
            self.process_text.SetLabelText('无有效URL/avBV号/URL加载中')
            return
        prefix, ext = os.path.splitext(self.video_ctrl.filename)
        cmd = ['ffmpeg', '-y', '-hide_banner']
        start = self.start_ts.timecode
        if start:
            cmd += ['-ss', str(start)]
            prefix += '_%s' % start
        else:
            prefix += '_0'
        end = self.end_ts.timecode
        if end:
            cmd += ['-to', str(end)]
            prefix += '_%s' % end
        else:
            prefix += '_-1'
        cmd += ['-user_agent', "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36"]
        for name, value in self.video_ctrl.headers.items():
            cmd += ['-headers', '%s: %s' % (name, value)]
        cmd += ['-i', self.video_ctrl.durl, '-c', 'copy', '-avoid_negative_ts', '1', prefix+ext]
        self.process_text.SetLabelText('开始下载，请检查命令行')
        print(cmd)
        p = subprocess.run(cmd)
        if p.returncode == 0:
            self.process_text.SetLabelText('下载完成：'+prefix+ext)
        else:
            self.process_text.SetLabelText('下载失败，请检查命令行')

if __name__ == '__main__':
    app = wx.App()
    frame = MainFrame()
    app.MainLoop()
