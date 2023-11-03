import pyee
import json
from ntwork.core.mgr import WeWorkMgr
from ntwork.const import notify_type, send_type
from threading import Event
from ntwork.wc import wcprobe
from ntwork.utils import generate_guid
from ntwork.utils import logger
from ntwork.exception import WeWorkNotLoginError
from functools import wraps
from typing import (
    List,
    Union,
    Tuple
)

log = logger.get_logger("WeWorkInstance")

class ReqData:
    __response_message = None
    msg_type: int = 0
    request_data = None

    def __init__(self, msg_type, data):
        self.msg_type = msg_type
        self.request_data = data
        self.__wait_event = Event()

    def wait_response(self, timeout=None):
        self.__wait_event.wait(timeout)
        return self.get_response_data()

    def on_response(self, message):
        self.__response_message = message
        self.__wait_event.set()

    def get_response_data(self):
        if self.__response_message is None:
            return None
        return self.__response_message["data"]


class RaiseExceptionFunc:
    def __init__(self, func):
        self.func = func

    def __call__(self, *args, **kwargs):
        try:
            self.func(*args, **kwargs)
        except Exception as e:
            log.error('callback error, in function `%s`, error: %s', self.func.__name__, e)


class WeWork:
    client_id: int = 0
    pid: int = 0
    status: bool = False
    login_status: bool = False

    def __init__(self):
        WeWorkMgr().append_instance(self)
        self.__wait_login_event = Event()
        self.__req_data_cache = {}
        self.event_emitter = pyee.EventEmitter()
        self.__login_info = {}

    def on(self, msg_type, f):
        if not (isinstance(msg_type, list) or isinstance(msg_type, tuple)):
            msg_type = [msg_type]
        for event in msg_type:
            self.event_emitter.on(str(event), RaiseExceptionFunc(f))

    def msg_register(self, msg_type: Union[int, List[int], Tuple[int]]):
        def wrapper(f):
            wraps(f)
            self.on(msg_type, f)
            return f

        return wrapper

    def on_close(self):
        self.login_status = False
        self.status = False
        self.event_emitter.emit(str(notify_type.MT_RECV_WEWORK_QUIT_MSG), self)

        message = {
            "type": notify_type.MT_RECV_WEWORK_QUIT_MSG,
            "data": {}
        }
        self.event_emitter.emit(str(notify_type.MT_ALL), self, message)

    def bind_client_id(self, client_id):
        self.status = True
        self.client_id = client_id

    def on_recv(self, message):
        log.debug("on recv message: %s", message)
        msg_type = message["type"]
        extend = message.get("extend", None)
        if msg_type == notify_type.MT_USER_LOGIN_MSG:
            self.login_status = True
            self.__wait_login_event.set()
            self.__login_info = message.get("data", {})
            log.info("login success, name: %s", self.__login_info["username"])
        elif msg_type == notify_type.MT_USER_LOGOUT_MSG:
            self.login_status = False
            log.info("logout, pid: %d", self.pid)

        if extend is not None and extend in self.__req_data_cache:
            req_data = self.__req_data_cache[extend]
            req_data.on_response(message)
            del self.__req_data_cache[extend]
        else:
            self.event_emitter.emit(str(msg_type), self, message)
            self.event_emitter.emit(str(notify_type.MT_ALL), self, message)

    def wait_login(self, timeout=None):
        log.info("wait login...")
        self.__wait_login_event.wait(timeout)

    def open(self, smart=False):
        self.pid = wcprobe.open(smart)
        log.info("open wework pid: %d", self.pid)
        return self.pid != 0

    def attach(self, pid: int):
        self.pid = pid
        log.info("attach wework pid: %d", self.pid)
        return wcprobe.attach(pid)

    def detach(self):
        log.info("detach wework pid: %d", self.pid)
        return wcprobe.detach(self.pid)
    
    def closure(self):
        log.info("closure wework pid: %d", self.pid)
        return wcprobe.exit()

    def __send(self, msg_type, data=None, extend=None):
        if not self.login_status:
            raise WeWorkNotLoginError()

        message = {
            'type': msg_type,
            'data': {} if data is None else data,
        }
        if extend is not None:
            message["extend"] = extend
        message_json = json.dumps(message)
        log.debug("communicate wework pid: %d,  data: %s", self.pid, message)
        return wcprobe.send(self.client_id, message_json)

    def __send_sync(self, msg_type, data=None, timeout=10):
        req_data = ReqData(msg_type, data)
        extend = self.__new_extend()
        self.__req_data_cache[extend] = req_data
        self.__send(msg_type, data, extend)
        return req_data.wait_response(timeout)

    def __new_extend(self):
        while True:
            guid = generate_guid("req")
            if guid not in self.__req_data_cache:
                return guid

    def __repr__(self):
        return f"WeWorkInstance(pid: {self.pid}, client_id: {self.client_id})"

    def get_login_info(self):
        """
        获取登录信息
        """
        return self.__login_info

    def exit_(self):
        """
        退出企业微信
        """
        data = {
        }
        return self.__send(send_type.MT_EXIT, data)

    def get_self_info(self):
        """
        获取自己个人信息跟登录信息类似
        """
        return self.__send_sync(send_type.MT_GET_SELF_MSG)

    def get_inner_contacts(self, page_num=1, page_size=500):
        """
        获取内部(同事)联系人列表
        """
        data = {
            'page_num': page_num,
            'page_size': page_size
        }
        return self.__send_sync(send_type.MT_GET_INNER_CONTACTS_MSG, data)

    def get_external_contacts(self, page_num=1, page_size=500):
        """
        获取外部(客户)联系人列表
        """
        data = {
            'page_num': page_num,
            'page_size': page_size
        }
        return self.__send_sync(send_type.MT_GET_EXTERNAL_CONTACTS_MSG, data)

    def get_contact_detail(self, user_id: str):
        """
        获取联系人详细信息
        """
        data = {
            'user_id': user_id
        }
        return self.__send_sync(send_type.MT_GET_CONTACT_DETAIL_MSG, data)

    def get_rooms(self, page_num=1, page_size=500):
        """
        获取群列表
        """
        data = {
            'page_num': page_num,
            'page_size': page_size
        }
        return self.__send_sync(send_type.MT_GET_ROOMS_MSG, data)

    def get_room_members(self, conversation_id: str, page_num: int = 1, page_size: int = 500):
        """
        获取群成员列表
        """
        data = {
            'conversation_id': conversation_id,
            'page_num': page_num,
            'page_size': page_size
        }
        return self.__send_sync(send_type.MT_GET_ROOM_MEMBERS_MSG, data)

    def send_text(self, conversation_id: str, content: str):
        """
        发送文本消息
        """
        data = {
            'conversation_id': conversation_id,
            'content': content
        }
        return self.__send(send_type.MT_SEND_TEXT_MSG, data)

    def send_room_at_msg(self, conversation_id: str, content: str, at_list: List[str]):
        """
        发送群@消息
        """
        data = {
            'conversation_id': conversation_id,
            'content': content,
            'at_list': at_list
        }
        return self.__send(send_type.MT_SEND_ROOM_AT_MSG, data)

    def send_card(self, conversation_id: str, user_id: str):
        """
        发送名片
        """
        data = {
            'conversation_id': conversation_id,
            'user_id': user_id
        }
        return self.__send(send_type.MT_SEND_CARD_MSG, data)

    def send_link_card(self, conversation_id: str, title: str, desc: str, url: str, image_url: str):
        """
        发送链接卡片
        """
        data = {
            'conversation_id': conversation_id,
            'title': title,
            'desc': desc,
            'url': url,
            'image_url': image_url
        }
        return self.__send(send_type.MT_SEND_LINK_MSG, data)

    def send_image(self, conversation_id: str, file_path: str):
        """
        发送图片
        """
        data = {
            'conversation_id': conversation_id,
            'file': file_path
        }
        return self.__send(send_type.MT_SEND_IMAGE_MSG, data)

    def send_file(self, conversation_id: str, file_path: str):
        """
        发送文件
        """
        data = {
            'conversation_id': conversation_id,
            'file': file_path
        }
        return self.__send(send_type.MT_SEND_FILE_MSG, data)

    #
    def send_video(self, conversation_id: str, file_path: str):
        """
        发送视频
        """
        data = {
            'conversation_id': conversation_id,
            'file': file_path
        }
        return self.__send(send_type.MT_SEND_VIDEO_MSG, data)

    def send_gif(self, conversation_id: str, file_path: str):
        """
        发送gif:
        """
        data = {
            'conversation_id': conversation_id,
            'file': file_path
        }
        return self.__send(send_type.MT_SEND_GIF_MSG, data)

    def send_voice(self, conversation_id: str, file_id: str, size: int, voice_time: int, aes_key: str, md5: str):
        """
        发送语音消息
        """
        data = {
            'conversation_id': conversation_id,
            'file_id': file_id,
            'size': size,
            'voice_time': voice_time,
            'aes_key': aes_key,
            'md5': md5
        }
        return self.__send(send_type.MT_SEND_VOICE_MSG, data)

    def cdn_upload(self, file_path: str, file_type: int):
        """
        上传cdn文件
        file_type:
                    1-图片/动图
                    4-视频
                    5-文件/语音
        """
        data = {
            'file_path': file_path,
            'file_type': file_type
        }
        return self.__send_sync(send_type.MT_CDN_UPLOAD_MSG, data)

    def c2c_cdn_download(self, file_id: str, aes_key: str, file_size: int, file_type: int, save_path: str):
        """
        下载c2c类型的cdn文件
        """
        data = {
            'file_id': file_id,
            'aes_key': aes_key,
            'file_size': file_size,
            'file_type': file_type,
            "save_path": save_path
        }
        return self.__send_sync(send_type.MT_C2CCDN_DOWNLOAD_MSG, data)

    def wx_cdn_download(self, url: str, auth_key: str, aes_key: str, size: int, save_path):
        """
        下载wx类型的cdn文件，以https开头
        """
        data = {
            'url': url,
            'auth_key': auth_key,
            'aes_key': aes_key,
            'size': size,
            'save_path': save_path
        }
        return self.__send_sync(send_type.MT_WXCDN_DOWNLOAD_MSG, data)

    def accept_friend(self, user_id: str, corp_id: str):
        """
        同意加好友请求
        """
        data = {
            "user_id": user_id,
            "corp_id": corp_id
        }
        return self.__send(send_type.MT_ACCEPT_FRIEND_MSG, data)

    def invite_to_room(self, user_list: list, conversation_id: str):
        """
        群添加成员
        """
        data = {
            "user_list": user_list,
            "conversation_id": conversation_id
        }
        return self.__send(send_type.MT_INVITE_TO_ROOM_MSG, data)

    def send_miniapp(self, aes_key: str, file_id: str, size: int, appicon: str, appid: str, appname: str,
                     conversation_id: str, page_path: str, title, username: str):
        """
        发送小程序
        """
        data = {
            "aes_key": aes_key,
            "file_id": file_id,
            "size": size,
            "appicon": appicon,
            "appid": appid,
            "appname": appname,
            "conversation_id": conversation_id,
            "page_path": page_path,
            "title": title,
            "username": username
        }
        return self.__send(send_type.MT_SEND_MINIAPP_MSG, data)

    def send_position(self, conversation_id: str, address: str, latitude: float, longitude: float, title: str,
                      zoom: int):
        """
        发送定位:
        """
        data = {
            'conversation_id': conversation_id,
            'address': address,
            'latitude': latitude,
            'longitude': longitude,
            'title': title,
            'zoom': zoom
        }
        return self.__send(send_type.MT_SEND_POSITION_MSG, data)

    def send_sph(self, conversation_id: str, avatar: str, cover_url: str, desc: str, feed_type: int, nickname: str,
                 thumb_url: str, url: str, extras: str):
        """
        发送视频号:
        """
        data = {
            'conversation_id': conversation_id,
            'avatar': avatar,
            'cover_url': cover_url,
            'desc': desc,
            'feed_type': feed_type,
            'nickname': nickname,
            'thumb_url': thumb_url,
            'url': url,
            'extras': extras
        }
        return self.__send(send_type.MT_SEND_SPH_MSG, data)

    def send_cdn_video(self, conversation_id: str, file_id: str, file_name: str, aes_key: str, md5: str, size: int,
                       video_duration: int):
        """
        发送CDN视频:
        """
        data = {
            'conversation_id': conversation_id,
            'file_id': file_id,
            'file_name': file_name,
            'aes_key': aes_key,
            'md5': md5,
            'size': size,
            'video_duration': video_duration
        }
        return self.__send(send_type.MT_SEND_CDN_VIDEO_MSG, data)

    def create_empty_room(self):
        """
        创建空外部群聊:
        """
        data = {}
        return self.__send_sync(send_type.MT_CREATE_EMPTY_ROOM_MSG, data)

    def add_card_user(self, user_id: str, corp_id: str, from_user_id: str, verify: str):
        """
        添加卡片联系人:
        """
        data = {
            "user_id": user_id,
            "corp_id": corp_id,
            "from_user_id": from_user_id,
            "verify": verify
        }
        return self.__send(send_type.MT_ADD_CARD_USER_MSG, data)

    def search_user(self, keyword: str):
        """
        搜索微信/企微用户:
        """
        data = {
            'keyword': keyword
        }
        return self.__send_sync(send_type.MT_SEARCH_USER_MSG, data)

    def get_tag_list(self):
        """
        获取标签列表信息:
        """
        data = {}
        return self.__send_sync(send_type.MT_DATA_TAG_LIST_MSG, data)

    def add_wechat_user(self, user_id: str, openid: str, wx_ticket: str, verify: str):
        """
        添加搜索的微信用户:
        """
        data = {
            "user_id": user_id,
            "openid": openid,
            "wx_ticket": wx_ticket,
            "verify": verify
        }
        return self.__send(send_type.MT_ADD_WECHAT_USER_MSG, data)

    def get_share_qrcode(self):
        """
        获取个人分享二维码:
        """
        data = {}
        return self.__send_sync(send_type.MT_SHARE_QRCODE_MSG, data)

    def del_user(self, user_id: str, corp_id: str):
        """
        删除联系人:
        """
        data = {
            "user_id": user_id,
            "corp_id": corp_id
        }
        return self.__send(send_type.MT_DEL_USER_MSG, data)
    
    def mod_room_name(self, conversation_id: str, name: str):
        """
        设置群名称，请确认该号有权限:
        """
        data = {
            "conversation_id": conversation_id,
            "name": name
        }
        return self.__send(send_type.MT_MOD_ROOM_NAME_MSG, data)

    def del_room_member(self, user_list: list, conversation_id: str):
        """
        批量移除群成员:
        """
        data = {
            "user_list": user_list,
            "conversation_id": conversation_id
        }
        return self.__send(send_type.MT_DEL_ROOM_MEMBER_MSG, data)

    def add_room_member(self, room_conversation_id: str, user_id: str,corp_id: str,verify: str):
        """
        添加群成员好友:
        """
        data = {
            "room_conversation_id": room_conversation_id,
            "user_id": user_id,
            "corp_id": corp_id,
            "verify": verify
        }
        return self.__send(send_type.MT_ADD_ROOM_MEMBER_MSG, data)


    def setup_group_invites(self, room_conversation_id: str, status: int):
        """
        开启/关闭群邀请确认.status值1为开启 0为关闭:
        """
        data = {
            "room_conversation_id": room_conversation_id,
            "status": status
        }
        return self.__send(send_type.MT_CONFIG_ROOM_INVITE_MSG, data)

    def setup_group_mod_name(self, room_conversation_id: str, status: int):
        """
        开启/关闭禁止修改群名.status值1为开启 0为关闭:
        """
        data = {
            "room_conversation_id": room_conversation_id,
            "status": status
        }
        return self.__send(send_type.MT_CONFIG_ROOM_MOD_NAME_MSG, data)

    def get_room_qrcode(self, room_conversation_id: str):
        """
        获取群聊二维码:
        """
        data = {
            "room_conversation_id": room_conversation_id
        }
        return self.__send_sync(send_type.MT_ROOM_QRCODE_MSG, data)

    def setup_user_remark(self, user_id: str, remark: str):
        """
        修改好友备注:
        """
        data = {
            "user_id": user_id,
            "remark": remark
          }
        return self.__send(send_type.MT_MOD_USER_REMARK_MSG, data)

    def setup_user_desc(self, user_id: str, desc: str):
        """
        修改好友描述:
        """
        data = {
            "user_id": user_id,
            "desc": desc
          }
        return self.__send(send_type.MT_MOD_USER_DESC_MSG, data)


    def setup_user_phone(self, phone_list: list, user_id: str):
        """
        修改客户手机号:
        """
        data = {
            "phone_list": phone_list,
            "user_id": user_id
          }
        return self.__send(send_type.MT_MOD_USER_PHONE_MSG, data)

    def setup_user_company(self, user_id: str, company: str):
        """
        修改客户公司名:
        """
        data = {
            "user_id": user_id,
            "company": company
          }
        return self.__send(send_type.MT_MOD_USER_COMPANY_MSG, data)

    def setup_user_tag(self, label_id: str, user_list: list):
        """
        设置客户标签:
        """
        data = {
            "label_id": label_id,
            "user_list": user_list
        }
        return self.__send(send_type.MT_TAG_USER_MSG, data)

    def get_user_tag(self, user_id: str):
        """
        获取指定客户的所有标签:
        """
        data = {
            "user_id": user_id
        }
        return self.__send_sync(send_type.MT_DATA_USER_TAG_MSG, data)

    def search_company(self, corp_id: str):
        """
        查询指定corp_id的公司名称和简称:
        """
        data = {
            "corp_id": corp_id
        }
        return self.__send_sync(send_type.MT_SEARCH_USER_CORP_MSG, data)


    def dissolve_room(self, room_conversation_id: str):
        """
        解散群聊:
        """
        data = {
            "room_conversation_id": room_conversation_id
        }
        return self.__send(send_type.MT_DISSOLVE_ROOM_MSG, data)

    def get_room_welcome(self):
        """
        获取群欢迎语列表:
        """
        data = {
        }
        return self.__send_sync(send_type.MT_ROOM_WELCOME_MSG, data)

    def send_sns_text(self, content: str, user_list: list = []):
        """
        发布文本到朋友圈:
        """
        data = {
            "content": content,
            "user_list": user_list
        }
        return self.__send(send_type.MT_SNS_SEND_MSG, data)

    def send_sns_link(self, title: str, content_url: str, user_list: List[str] = [],content: str = ""):
        """
        发布链接到朋友圈:
        """
        data = {
            "content": content,
            "user_list": user_list,
            "link_info": {
                "title": title,
                "content_url": content_url
            }
        }
        return self.__send(send_type.MT_SNS_SEND_MSG, data)

    def send_sns_sign(self, sign: str):
        """
        设置朋友圈签名:
        """
        data = {
            "sign": sign
        }
        return self.__send(send_type.MT_SNS_SIGN_MSG, data)

    def search_company_details(self, corp_id: str):
        """
        查询指定corp_id的公司详细信息:
        """
        data = {
            "corp_id": corp_id
        }
        return self.__send_sync(send_type.MT_SEARCH_CORP_MSG, data)

    def get_sns_list(self):
        """
        获取朋友圈列表:
        """
        data = {
        }
        return self.__send_sync(send_type.MT_SNS_LIST_MSG, data)

    def get_del_by_user_list(self, page_num: int,page_size: int):
        """
        获取被对方删除的客户列表:
        """
        data = {
            "page_num": page_num,
            "page_size": page_size
        }
        return self.__send_sync(send_type.MT_DEL_BY_USER_LIST_MSG, data)

    def get_me_company_list(self):
        """
        获取当前账号公司信息列表:
        """
        data = {}
        return self.__send_sync(send_type.MT_GET_ME_COMPANY_LIST_MSG, data)

    def add_room_admin(self, room_conversation_id: str,user_list: list):
        """
        添加群管理员:
        """
        data = {
            "room_conversation_id": room_conversation_id,
            "user_list": user_list
        }
        return self.__send(send_type.MT_ADD_ROOM_ADMIN_MSG, data)

    def del_room_admin(self, room_conversation_id: str,user_list: list):
        """
        删除群管理员:
        """
        data = {
            "room_conversation_id": room_conversation_id,
            "user_list": user_list
        }
        return self.__send(send_type.MT_DEL_ROOM_ADMIN_MSG, data)

    def exit_room(self, room_conversation_id: str):
        """
        退出指定群聊:
        """
        data = {
            "room_conversation_id": room_conversation_id
        }
        return self.__send(send_type.MT_EXIT_ROOM_MSG, data)

    def send_text_v2(self, conversation_id: str, content: str):
        """
        发送文本信息(新版)
        """
        data = {
            "conversation_id": conversation_id,
            "content": content
        }
        return self.__send(send_type.MT_SEND_TEXT_V2_MSG, data)

    def send_img_v2(self, conversation_id: str, file_id: str,file_name: str,
                    image_width: int,image_height: int,aes_key: str,md5: str,size: int):
        """
        发送CDN图片文件
        """
        data = {
            "conversation_id": conversation_id,
            "file_id": file_id,
            "file_name": file_name,
            "image_width": image_width,
            "image_height": image_height,
            "aes_key": aes_key,
            "md5": md5,
            "size": size
            }
        return self.__send(send_type.MT_SEND_IMG_V2_MSG, data)

    def send_video_v2(self, conversation_id: str, file_id: str,file_name: str,
                      aes_key: str,md5: str,size: int,video_duration: int):
        """
        发送CDN视频文件
        """
        data = {
            'conversation_id': conversation_id,
            "file_id": file_id,
            "file_name": file_name,
            "aes_key": aes_key,
            "md5": md5,
            "size": size,
            "video_duration": video_duration
            }
        return self.__send(send_type.MT_SEND_VIDEO_V2_MSG, data)

    def send_gif_v2(self, conversation_id: str, url: str):
        """
        转发URL表情包
        """
        data = {
            'conversation_id': conversation_id,
            'url': url
            }
        return self.__send(send_type.MT_SEND_GIF_V2_MSG, data)

    def send_link_v2(self, conversation_id: str, url: str,image_url: str,title: str,desc: str):
        """
        发送链接卡片（新版）
        """
        data = {
            "conversation_id": conversation_id,
            "url": url,
            "image_url": image_url,
            "title": title,
            "desc": desc
        }
        return self.__send(send_type.MT_SEND_LINK_V2_MSG, data)

    def send_room_at_v2(self, conversation_id: str, content: str,at_list: list):
        """
        发送群@消息(新版)
        """
        data = {
            "conversation_id": conversation_id,
            "content": content,
            "at_list": at_list
        }
        return self.__send(send_type.MT_SEND_CHATROOM_AT_V2_MSG, data)

    def send_live(self, conversation_id: str, avatar: str,cover_url: str,desc: str,extras: str,
                  feed_type: int,nickname: str,object_id: str,object_nonce_id: str,thumb_url: str,url: str):
        """
        发送视频号直播信息
        """
        data = {
            "conversation_id": conversation_id,
            "avatar": avatar,
            "cover_url": cover_url,
            "desc": desc,
            "extras": extras,
            "feed_type": feed_type,
            "nickname": nickname,
            "object_id": object_id,
            "object_nonce_id": object_nonce_id,
            "thumb_url": thumb_url,
            "url": url
        }
        return self.__send(send_type.MT_SEND_LIVE_MSG, data)


