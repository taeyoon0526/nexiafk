from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import re

import discord
from discord.ext import tasks
from redbot.core import Config, commands

log = logging.getLogger("red.nexiafk")


DEFAULT_ALLOWED_USER_ID = 1173942304927645786
DEFAULT_MESSAGE = (
    "잠시 자리를 비웠습니다. 용건은 남겨주시면 확인 후 답장드리겠습니다."
)


def _now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def _parse_duration(value: str) -> int | None:
    value = value.strip().lower()
    if not value:
        return None
    m = re.fullmatch(r"(\d+)([smhd])", value)
    if not m:
        return None
    num = int(m.group(1))
    unit = m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return num * mult


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0초"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}일")
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    if sec and not parts:
        parts.append(f"{sec}초")
    return " ".join(parts) if parts else "0초"


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
            enable_offduty_autofk=False,
            offduty_tag="[OFFDUTY]",
        )

        self._auto_task.start()

    def cog_unload(self) -> None:
        self._auto_task.cancel()

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
    async def _safe_send_embed(
        self, message: discord.Message, embed: discord.Embed
    ) -> None:
        try:
            await message.reply(embed=embed, mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await message.channel.send(embed=embed)
            except Exception:
                log.exception("임베드 reply/send 모두 실패")

    async def _safe_dm(self, user: discord.abc.User, embed: discord.Embed) -> None:
        try:
            await user.send(embed=embed)
        except Exception:
            log.exception("DM 전송 실패")

    async def _safe_ctx_send_embed(
        self, ctx: commands.Context, embed: discord.Embed
    ) -> None:
        try:
            await ctx.send(embed=embed)
        except Exception:
            log.exception("임베드 ctx.send 실패")


    async def _ensure_allowed(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return False
        allowed = await self.config.guild(ctx.guild).allowed_user_ids()
        if ctx.author.id not in allowed:
            await ctx.send("권한이 없습니다.")
            return False
        return True

    def _default_entry(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "since_ts": 0,
            "message_override": None,
            "last_auto_reply_ts": 0,
            "auto_clear_on_message": True,
            "auto_afk_seconds": 0,
            "auto_afk_enabled": False,
            "last_activity_ts": 0,
        }

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
                self._default_entry(),
            )
            entry.setdefault("auto_clear_on_message", True)
            if not entry.get("enabled"):
                entry["enabled"] = True
                entry["since_ts"] = now
                msg = entry.get("message_override") or (
                    await self.config.guild(ctx.guild).guild_default_message()
                )
                embed = discord.Embed(title="AFK 활성화됨")
                embed.add_field(name="메시지", value=msg, inline=False)
                await self._safe_ctx_send_embed(ctx, embed)
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
                embed = discord.Embed(title="AFK 해제됨")
                await self._safe_ctx_send_embed(ctx, embed)
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
                self._default_entry(),
            )
            entry.setdefault("auto_clear_on_message", True)
            enabled = entry.get("enabled", False)
            since_ts = int(entry.get("since_ts", 0) or 0)
            if since_ts > 0:
                since_txt = f"<t:{since_ts}:R> (<t:{since_ts}:f>)"
            else:
                since_txt = "N/A"
            msg = entry.get("message_override") or (
                await self.config.guild(ctx.guild).guild_default_message()
            )
            auto_clear = "ON" if entry.get("auto_clear_on_message", True) else "OFF"
            embed = discord.Embed(title="AFK 상태")
            embed.add_field(name="상태", value="ON" if enabled else "OFF", inline=False)
            embed.add_field(name="AFK 시작", value=since_txt, inline=False)
            embed.add_field(name="메시지", value=msg, inline=False)
            embed.add_field(name="자동 해제", value=auto_clear, inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
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
                self._default_entry(),
            )
            entry.setdefault("auto_clear_on_message", True)
            entry["message_override"] = message
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
            embed = discord.Embed(title="개인 AFK 멘트를 설정했습니다.")
            embed.add_field(name="메시지", value=message, inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
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
                self._default_entry(),
            )
            entry.setdefault("auto_clear_on_message", True)
            entry["message_override"] = None
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
            embed = discord.Embed(title="개인 AFK 멘트를 삭제했습니다.")
            await self._safe_ctx_send_embed(ctx, embed)
        except Exception:
            log.exception("AFK 멘트 삭제 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    
    
    @afk_group.command(name="auto")
    async def afk_auto(self, ctx: commands.Context, duration: Optional[str] = None) -> None:
        """활동이 없을 때 자동 AFK 설정/토글."""
        if not await self._ensure_allowed(ctx):
            return
        try:
            state = await self.config.guild(ctx.guild).afk_state()
            key = str(ctx.author.id)
            entry = state.get(key, self._default_entry())
            entry.setdefault("auto_afk_enabled", False)
            entry.setdefault("auto_afk_seconds", 0)
            entry.setdefault("last_activity_ts", 0)

            if duration is None:
                if entry.get("auto_afk_seconds", 0) <= 0:
                    embed = discord.Embed(title="자동 AFK", description="먼저 시간(예: 10m, 1h, 1d)을 설정해주세요.")
                    await self._safe_ctx_send_embed(ctx, embed)
                    return
                entry["auto_afk_enabled"] = not entry.get("auto_afk_enabled", False)
                state[key] = entry
                await self.config.guild(ctx.guild).afk_state.set(state)
                embed = discord.Embed(title="자동 AFK 토글")
                embed.add_field(name="상태", value="ON" if entry.get("auto_afk_enabled") else "OFF", inline=False)
                embed.add_field(name="시간", value=_format_duration(int(entry.get("auto_afk_seconds"))) , inline=False)
                await self._safe_ctx_send_embed(ctx, embed)
                return

            seconds = _parse_duration(duration)
            if seconds is None or seconds <= 0:
                embed = discord.Embed(title="자동 AFK", description="시간 형식이 올바르지 않습니다. 예: 10m, 1h, 1d")
                await self._safe_ctx_send_embed(ctx, embed)
                return

            entry["auto_afk_seconds"] = seconds
            entry["auto_afk_enabled"] = True
            entry["last_activity_ts"] = _now_ts()
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
            embed = discord.Embed(title="자동 AFK 설정 완료")
            embed.add_field(name="시간", value=_format_duration(seconds), inline=False)
            embed.add_field(name="상태", value="ON", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
        except Exception:
            log.exception("자동 AFK 설정 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_group.command(name="autoclear")
    async def afk_autoclear(self, ctx: commands.Context, mode: Optional[str] = None) -> None:
        """메시지 전송 시 AFK 자동 해제 토글."""
        if not await self._ensure_allowed(ctx):
            return
        if mode is None:
            try:
                state = await self.config.guild(ctx.guild).afk_state()
                key = str(ctx.author.id)
                entry = state.get(key, self._default_entry())
                entry.setdefault("auto_clear_on_message", True)
                embed = discord.Embed(title="자동 해제 상태")
                embed.add_field(
                    name="자동 해제",
                    value="ON" if entry.get("auto_clear_on_message", True) else "OFF",
                    inline=False,
                )
                await self._safe_ctx_send_embed(ctx, embed)
            except Exception:
                log.exception("자동 해제 상태 조회 실패")
                await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
            return

        mode_l = mode.lower()
        if mode_l in {"on", "true", "enable", "enabled", "1"}:
            value = True
        elif mode_l in {"off", "false", "disable", "disabled", "0"}:
            value = False
        else:
            await ctx.send("값은 on/off 중 하나여야 합니다.")
            return

        try:
            state = await self.config.guild(ctx.guild).afk_state()
            key = str(ctx.author.id)
            entry = state.get(key, self._default_entry())
            entry["auto_clear_on_message"] = value
            state[key] = entry
            await self.config.guild(ctx.guild).afk_state.set(state)
            embed = discord.Embed(title="자동 해제 설정 완료")
            embed.add_field(name="자동 해제", value="ON" if value else "OFF", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
        except Exception:
            log.exception("자동 해제 설정 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @commands.group(name="afkadmin", invoke_without_command=True)
    @commands.is_owner()
    async def afk_admin(self, ctx: commands.Context) -> None:
        """AFK 허용 사용자 관리."""
        embed = discord.Embed(title="AFK 관리자")
        embed.add_field(name="명령어", value="add/remove/list/reset/setdefault/toggledefault/togglebots/toggleoffduty", inline=False)
        await self._safe_ctx_send_embed(ctx, embed)

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
            embed = discord.Embed(title="허용 사용자 추가")
            embed.add_field(name="사용자", value=f"{user} ({user.id})", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
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
            embed = discord.Embed(title="허용 사용자 제거")
            embed.add_field(name="사용자", value=f"{user} ({user.id}){warn}", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
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
            embed = discord.Embed(title="허용 사용자 목록")
            embed.description = " ".join(mentions)
            await self._safe_ctx_send_embed(ctx, embed)
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
            embed = discord.Embed(title="허용 사용자 목록 초기화")
            await self._safe_ctx_send_embed(ctx, embed)
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
            embed = discord.Embed(title="기본 멘트 변경 허용")
            embed.add_field(name="상태", value="ON" if not current else "OFF", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
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
            embed = discord.Embed(title="봇 메시지 무시")
            embed.add_field(name="상태", value="ON" if not current else "OFF", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
        except Exception:
            log.exception("togglebots 실패")
            await ctx.send("일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    @afk_admin.command(name="toggleoffduty")
    @commands.is_owner()
    async def afk_admin_toggleoffduty(self, ctx: commands.Context) -> None:
        """[OFFDUTY] 닉네임 자동 AFK 토글."""
        if ctx.guild is None:
            await ctx.send("이 명령어는 DM에서 사용할 수 없습니다.")
            return
        try:
            current = await self.config.guild(ctx.guild).enable_offduty_autofk()
            await self.config.guild(ctx.guild).enable_offduty_autofk.set(not current)
            embed = discord.Embed(title="[OFFDUTY] 자동 AFK")
            embed.add_field(name="상태", value="ON" if not current else "OFF", inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
        except Exception:
            log.exception("toggleoffduty 실패")
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
            embed = discord.Embed(title="기본 AFK 멘트 변경")
            embed.add_field(name="메시지", value=message, inline=False)
            await self._safe_ctx_send_embed(ctx, embed)
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

        allowed = set(conf.get("allowed_user_ids", []))
        afk_state = conf.get("afk_state", {})

        # 활동 기록 업데이트
        author_key = str(message.author.id)
        if message.author.id in allowed:
            entry = afk_state.get(author_key, self._default_entry())
            entry.setdefault("last_activity_ts", 0)
            entry["last_activity_ts"] = _now_ts()
            afk_state[author_key] = entry
            try:
                await self.config.guild(message.guild).afk_state.set(afk_state)
            except Exception:
                log.exception("활동 기록 저장 실패")

        # [OFFDUTY] 자동 AFK (길드 메시지 없음)
        if conf.get("enable_offduty_autofk") and message.author.id in allowed:
            tag = conf.get("offduty_tag") or "[OFFDUTY]"
            display_name = getattr(message.author, "display_name", message.author.name)
            entry = afk_state.get(author_key, self._default_entry())
            if tag in display_name and not entry.get("enabled"):
                entry["enabled"] = True
                entry["since_ts"] = _now_ts()
                entry["last_auto_reply_ts"] = 0
                afk_state[author_key] = entry
                try:
                    await self.config.guild(message.guild).afk_state.set(afk_state)
                except Exception:
                    log.exception("OFFDUTY 자동 AFK 저장 실패")


        # AFK 사용자가 메시지를 보내면 자동 해제 (기본 ON)
        author_key = str(message.author.id)
        author_entry = afk_state.get(author_key)
        if (
            message.author.id in allowed
            and author_entry
            and author_entry.get("enabled")
            and author_entry.get("auto_clear_on_message", True)
        ):
            since_ts = int(author_entry.get("since_ts", 0) or 0)
            author_entry["enabled"] = False
            author_entry["since_ts"] = 0
            author_entry["last_auto_reply_ts"] = 0
            afk_state[author_key] = author_entry
            try:
                await self.config.guild(message.guild).afk_state.set(afk_state)
            except Exception:
                log.exception("자동 해제 저장 실패")
            since_text = f"<t:{since_ts}:R>" if since_ts > 0 else "N/A"
            welcome_embed = discord.Embed(title="돌아오신걸 환영합니다!")
            welcome_embed.add_field(name="AFK 지속시간", value=since_text, inline=False)
            await self._safe_send_embed(message, welcome_embed)

        if not message.mentions:
            return

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
        afk_embed = discord.Embed(title="AFK 알림")
        afk_embed.add_field(name="대상", value=target_member.mention, inline=False)
        afk_embed.add_field(name="메시지", value=msg_text, inline=False)
        afk_embed.add_field(name="AFK 시작", value=since_text, inline=False)

        await self._safe_send_embed(message, afk_embed)

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


    @tasks.loop(seconds=60)
    async def _auto_task(self) -> None:
        now = _now_ts()
        for guild in self.bot.guilds:
            try:
                conf = await self.config.guild(guild).all()
            except Exception:
                log.exception("Config 읽기 실패(자동 AFK)")
                continue
            allowed = set(conf.get("allowed_user_ids", []))
            afk_state = conf.get("afk_state", {})
            changed = False
            for uid_str, entry in afk_state.items():
                try:
                    uid = int(uid_str)
                except ValueError:
                    continue
                if uid not in allowed:
                    continue
                entry.setdefault("auto_afk_enabled", False)
                entry.setdefault("auto_afk_seconds", 0)
                entry.setdefault("last_activity_ts", 0)
                if entry.get("enabled"):
                    continue
                if not entry.get("auto_afk_enabled"):
                    continue
                seconds = int(entry.get("auto_afk_seconds") or 0)
                if seconds <= 0:
                    continue
                last_act = int(entry.get("last_activity_ts") or 0)
                if last_act <= 0:
                    continue
                if now - last_act < seconds:
                    continue
                entry["enabled"] = True
                entry["since_ts"] = now
                entry["last_auto_reply_ts"] = 0
                afk_state[uid_str] = entry
                changed = True
                member = guild.get_member(uid)
                if member is not None:
                    embed = discord.Embed(title="AFK 자동 활성화")
                    embed.add_field(name="서버", value=guild.name, inline=False)
                    embed.add_field(name="AFK 시작", value=f"<t:{now}:R>", inline=False)
                    await self._safe_dm(member, embed)
            if changed:
                try:
                    await self.config.guild(guild).afk_state.set(afk_state)
                except Exception:
                    log.exception("자동 AFK 저장 실패")

    @_auto_task.before_loop
    async def _before_auto_task(self) -> None:
        await self.bot.wait_until_red_ready()


    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild is None:
            return
        try:
            conf = await self.config.guild(after.guild).all()
        except Exception:
            log.exception("Config 읽기 실패(멤버 업데이트)")
            return
        if not conf.get("enable_offduty_autofk"):
            return
        allowed = set(conf.get("allowed_user_ids", []))
        if after.id not in allowed:
            return
        tag = conf.get("offduty_tag") or "[OFFDUTY]"
        if tag not in after.display_name:
            return
        state = conf.get("afk_state", {})
        key = str(after.id)
        entry = state.get(key, self._default_entry())
        if entry.get("enabled"):
            return
        entry["enabled"] = True
        entry["since_ts"] = _now_ts()
        entry["last_auto_reply_ts"] = 0
        state[key] = entry
        try:
            await self.config.guild(after.guild).afk_state.set(state)
        except Exception:
            log.exception("OFFDUTY 자동 AFK 저장 실패(멤버 업데이트)")
