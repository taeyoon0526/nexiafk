from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord
from redbot.core import Config, commands

log = logging.getLogger("red.nexiafk")


DEFAULT_ALLOWED_USER_ID = 1173942304927645786
DEFAULT_MESSAGE = (
    "잠시 자리를 비웠습니다. 용건은 남겨주시면 확인 후 답장드리겠습니다."
)


def _now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


class NexiAFK(commands.Cog):
    """특정 사용자만 AFK를 사용하고 멘션 시 자동 응답하는 Cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=473920164028, force_registration=True)
        self.config.register_guild(
            allowed_user_ids=[DEFAULT_ALLOWED_USER_ID],
            guild_default_message=DEFAULT_MESSAGE,
            afk_state={},
            cooldown_seconds=30,
            per_channel_cooldown=True,
            logging_enabled=False,
            log_channel_id=None,
            enable_owner_default_message_edit=False,
            ignore_bots=True,
        )

    async def _send_log(
        self,
        guild: discord.Guild,
        action: str,
        description: str,
        *,
        channel: Optional[discord.abc.GuildChannel] = None,
        target: Optional[discord.Member | discord.User] = None,
        mentioner: Optional[discord.Member | discord.User] = None,
        result: Optional[str] = None,
    ) -> None:
        try:
            conf = await self.config.guild(guild).all()
            if not conf.get("logging_enabled") or not conf.get("log_channel_id"):
                return
            log_channel = guild.get_channel(conf["log_channel_id"])
            if log_channel is None:
                return
            embed = discord.Embed(title="NexiAFK", description=description)
            embed.add_field(name="Action", value=action, inline=True)
            if channel is not None:
                embed.add_field(name="Channel", value=channel.mention, inline=True)
            if target is not None:
                embed.add_field(name="Target", value=f"{target} ({target.id})", inline=False)
            if mentioner is not None:
                embed.add_field(
                    name="Mentioner", value=f"{mentioner} ({mentioner.id})", inline=False
                )
            if result is not None:
                embed.add_field(name="Result", value=result, inline=False)
            await log_channel.send(embed=embed)
        except Exception:
            log.exception("로그 전송 실패")

    async def _safe_send(
        self, message: discord.Message, content: str
    ) -> None:
        try:
            await message.reply(content, mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await message.channel.send(content)
            except Exception:
                log.exception("reply/send 모두 실패")
                if message.guild is not None:
                    await self._send_log(
                        message.guild,
                        action="ERROR",
                        description="reply/send 실패",
                        channel=message.channel,
                        mentioner=message.author,
                        result="권한 부족 또는 전송 실패",
                    )

    async def _ensure_allowed(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return False
        allowed = await self.config.guild(ctx.guild).allowed_user_ids()
        if ctx.author.id not in allowed:
            await ctx.send("권한이 없습니다.")
            return False
        return True

    def _get_last_ts(
        self,
        entry: Dict[str, Any],
        channel_id: int,
        per_channel: bool,
    ) -> int:
        last_val = entry.get("last_auto_reply_ts", 0)
        if per_channel:
            if isinstance(last_val, dict):
                return int(last_val.get(str(channel_id), 0))
            return 0
        if isinstance(last_val, dict):
            return 0
        return int(last_val or 0)

    def _set_last_ts(
        self,
        entry: Dict[str, Any],
        channel_id: int,
        per_channel: bool,
        ts: int,
    ) -> None:
        if per_channel:
            last_val = entry.get("last_auto_reply_ts", {})
            if not isinstance(last_val, dict):
                last_val = {}
            last_val[str(channel_id)] = ts
            entry["last_auto_reply_ts"] = last_val
        else:
            entry["last_auto_reply_ts"] = ts

    @commands.group(name="afk", invoke_without_command=True)
    async def afk_group(self, ctx: commands.Context) -> None:
        """AFK 토글."""
        if not await self._ensure_allowed(ctx):
            return
        now = _now_ts()
        try:
            state = await self.config.guild(ctx.guild).afk_state()
            key = str(ctx.author.id)
            entry = state.get(
                key,
                {"enabled": False, "since_ts": 0, "message_override": None, "last_auto_reply_ts": 0},
            )
            if not entry.get("enabled"):
                entry["enabled"] = True
                entry["since_ts"] = now
                await ctx.send(
                    f"AFK 활성화됨\n메시지: {entry.get('message_override') or (await self.config.guild(ctx.guild).guild_default_message())}"
                )
                await self._send_log(
                    ctx.guild,
                    action="AFK ON",
                    description="AFK 활성화",
                    target=ctx.author,
                    result=entry.get("message_override") or "기본 멘트",
                )
            else:
                entry["enabled"] = False
                entry["since_ts"] = 0
                entry["last_auto_reply_ts"] = 0
                await ctx.send("AFK 해제됨")
                await self._send_log(
                    ctx.guild,
                    action="AFK OFF",
                    description="AFK 해제",
                    target=ctx.author,
                )
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
        except Exception:
            log.exception("AFK 토글 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_group.command(name="status")
    async def afk_status(self, ctx: commands.Context) -> None:
        """AFK 상태 확인."""
        if not await self._ensure_allowed(ctx):
            return
        try:
            state = await self.config.guild(ctx.guild).afk_state()
            key = str(ctx.author.id)
            entry = state.get(
                key,
                {"enabled": False, "since_ts": 0, "message_override": None, "last_auto_reply_ts": 0},
            )
            enabled = entry.get("enabled", False)
            since_ts = int(entry.get("since_ts", 0) or 0)
            if since_ts > 0:
                since_txt = f"<t:{since_ts}:R> (<t:{since_ts}:f>)"
            else:
                since_txt = "N/A"
            msg = entry.get("message_override") or (
                await self.config.guild(ctx.guild).guild_default_message()
            )
            await ctx.send(
                f"AFK 상태: {'ON' if enabled else 'OFF'}\nAFK 시작: {since_txt}\n메시지: {msg}"
            )
        except Exception:
            log.exception("AFK 상태 조회 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_group.command(name="set")
    async def afk_set(self, ctx: commands.Context, *, message: str) -> None:
        """개인 AFK 멘트 설정."""
        if not await self._ensure_allowed(ctx):
            return
        message = message.strip()
        if not (1 <= len(message) <= 200):
            await ctx.send("메시지는 1~200자여야 합니다.")
            return
        if len(message.splitlines()) > 3:
            await ctx.send("줄바꿈은 최대 3줄까지 허용됩니다.")
            return
        try:
            state = await self.config.guild(ctx.guild).afk_state()
            key = str(ctx.author.id)
            entry = state.get(
                key,
                {"enabled": False, "since_ts": 0, "message_override": None, "last_auto_reply_ts": 0},
            )
            entry["message_override"] = message
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
            await ctx.send("개인 AFK 멘트를 설정했습니다.")
        except Exception:
            log.exception("AFK 멘트 설정 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_group.command(name="clearmsg")
    async def afk_clearmsg(self, ctx: commands.Context) -> None:
        """개인 AFK 멘트 삭제."""
        if not await self._ensure_allowed(ctx):
            return
        try:
            state = await self.config.guild(ctx.guild).afk_state()
            key = str(ctx.author.id)
            entry = state.get(
                key,
                {"enabled": False, "since_ts": 0, "message_override": None, "last_auto_reply_ts": 0},
            )
            entry["message_override"] = None
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
            await ctx.send("개인 AFK 멘트를 삭제했습니다.")
        except Exception:
            log.exception("AFK 멘트 삭제 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @commands.group(name="afkadmin", invoke_without_command=True)
    @commands.is_owner()
    async def afk_admin(self, ctx: commands.Context) -> None:
        """AFK 허용 사용자 관리."""
        await ctx.send("사용 가능한 하위 명령어: add/remove/list/reset/setdefault/toggledefault/togglebots")

    @afk_admin.command(name="add")
    @commands.is_owner()
    async def afk_admin_add(self, ctx: commands.Context, user: discord.User) -> None:
        """허용 사용자 추가."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            allowed = await self.config.guild(ctx.guild).allowed_user_ids()
            if user.id in allowed:
                await ctx.send("이미 허용된 사용자입니다.")
                return
            if len(allowed) >= 50:
                await ctx.send("허용 사용자 수는 최대 50명까지 가능합니다.")
                return
            allowed.append(user.id)
            await self.config.guild(ctx.guild).allowed_user_ids.set(allowed)
            await ctx.send(f"허용 사용자에 추가했습니다: {user} ({user.id})")
        except Exception:
            log.exception("허용 사용자 추가 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="remove")
    @commands.is_owner()
    async def afk_admin_remove(self, ctx: commands.Context, user: discord.User) -> None:
        """허용 사용자 제거."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            allowed = await self.config.guild(ctx.guild).allowed_user_ids()
            if user.id not in allowed:
                await ctx.send("해당 사용자는 허용 목록에 없습니다.")
                return
            allowed.remove(user.id)
            await self.config.guild(ctx.guild).allowed_user_ids.set(allowed)
            warn = ""
            if user.id == DEFAULT_ALLOWED_USER_ID:
                warn = " (기본 허용 사용자 제거됨)"
            await ctx.send(f"허용 사용자에서 제거했습니다: {user} ({user.id}){warn}")
        except Exception:
            log.exception("허용 사용자 제거 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="list")
    @commands.is_owner()
    async def afk_admin_list(self, ctx: commands.Context) -> None:
        """허용 사용자 목록 확인."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            allowed = await self.config.guild(ctx.guild).allowed_user_ids()
            mentions = []
            for uid in allowed:
                member = ctx.guild.get_member(uid)
                mentions.append(member.mention if member else f"<@{uid}>")
            await ctx.send("허용 사용자 목록:\n" + " ".join(mentions))
        except Exception:
            log.exception("허용 사용자 목록 조회 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="reset")
    @commands.is_owner()
    async def afk_admin_reset(self, ctx: commands.Context) -> None:
        """허용 사용자 목록 초기화."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            await self.config.guild(ctx.guild).allowed_user_ids.set([DEFAULT_ALLOWED_USER_ID])
            await ctx.send("허용 사용자 목록을 기본값으로 초기화했습니다.")
        except Exception:
            log.exception("허용 사용자 초기화 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="toggledefault")
    @commands.is_owner()
    async def afk_admin_toggledefault(self, ctx: commands.Context) -> None:
        """기본 멘트 변경 허용 토글."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            current = await self.config.guild(ctx.guild).enable_owner_default_message_edit()
            await self.config.guild(ctx.guild).enable_owner_default_message_edit.set(
                not current
            )
            await ctx.send(
                f"기본 멘트 변경 허용: {'ON' if not current else 'OFF'}"
            )
        except Exception:
            log.exception("toggledefault 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="togglebots")
    @commands.is_owner()
    async def afk_admin_togglebots(self, ctx: commands.Context) -> None:
        """봇 메시지 무시 토글."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            current = await self.config.guild(ctx.guild).ignore_bots()
            await self.config.guild(ctx.guild).ignore_bots.set(not current)
            await ctx.send(f"봇 메시지 무시: {'ON' if not current else 'OFF'}")
        except Exception:
            log.exception("togglebots 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="setdefault")
    @commands.is_owner()
    async def afk_admin_setdefault(self, ctx: commands.Context, *, message: str) -> None:
        """길드 기본 AFK 멘트 변경."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            enabled = await self.config.guild(ctx.guild).enable_owner_default_message_edit()
            if not enabled:
                await ctx.send("기본 멘트 변경 기능이 비활성화되어 있습니다.")
                return
            message = message.strip()
            if not (1 <= len(message) <= 200):
                await ctx.send("메시지는 1~200자여야 합니다.")
                return
            await self.config.guild(ctx.guild).guild_default_message.set(message)
            await ctx.send("기본 AFK 멘트를 변경했습니다.")
        except Exception:
            log.exception("기본 멘트 변경 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin_add.error
    async def afk_admin_add_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            raw = " ".join(ctx.message.content.split()[2:]) if ctx.message else ""
            await ctx.send(f"유저 파싱 실패: {raw or '입력값 없음'}")
            return
        raise error

    @afk_admin_remove.error
    async def afk_admin_remove_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            raw = " ".join(ctx.message.content.split()[2:]) if ctx.message else ""
            await ctx.send(f"유저 파싱 실패: {raw or '입력값 없음'}")
            return
        raise error

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.webhook_id is not None:
            return
        try:
            conf = await self.config.guild(message.guild).all()
        except Exception:
            log.exception("Config 읽기 실패")
            return
        if conf.get("ignore_bots") and message.author.bot:
            return
        if not message.mentions:
            return

        allowed = set(conf.get("allowed_user_ids", []))
        afk_state = conf.get("afk_state", {})
        target_member: Optional[discord.Member] = None
        target_entry: Optional[Dict[str, Any]] = None

        for mention in message.mentions:
            if mention.id == message.author.id:
                continue
            if mention.id not in allowed:
                continue
            entry = afk_state.get(str(mention.id))
            if not entry or not entry.get("enabled"):
                continue
            target_member = mention
            target_entry = entry
            break

        if target_member is None or target_entry is None:
            return

        cooldown_seconds = int(conf.get("cooldown_seconds", 30) or 30)
        per_channel = bool(conf.get("per_channel_cooldown", True))
        last_ts = self._get_last_ts(target_entry, message.channel.id, per_channel)
        now = _now_ts()
        if last_ts + cooldown_seconds > now:
            return

        msg_override = target_entry.get("message_override")
        default_msg = conf.get("guild_default_message") or DEFAULT_MESSAGE
        msg_text = msg_override or default_msg
        since_ts = int(target_entry.get("since_ts", 0) or 0)
        since_text = f"<t:{since_ts}:R>" if since_ts > 0 else "N/A"
        content = (
            f"{target_member.display_name}님은 현재 AFK입니다.\n"
            f"메시지: {msg_text}\n"
            f"AFK 시작: {since_text}"
        )

        await self._safe_send(message, content)

        try:
            self._set_last_ts(target_entry, message.channel.id, per_channel, now)
            afk_state[str(target_member.id)] = target_entry
            await self.config.guild(message.guild).afk_state.set(afk_state)
            await self._send_log(
                message.guild,
                action="AUTO REPLY",
                description="멘션 자동 응답 전송",
                channel=message.channel,
                target=target_member,
                mentioner=message.author,
                result=msg_text[:100],
            )
        except Exception:
            log.exception("자동 응답 후 상태 저장 실패")
