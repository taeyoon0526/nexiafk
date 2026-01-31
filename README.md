# nexiafk_repo

Red-DiscordBot v3용 NexiAFK Cog 레포지토리입니다.

## 설치
- `!repo add nexiafk_repo https://github.com/taeyoon0526/nexiafk`
- `!cog list nexiafk_repo`
- `!cog install nexiafk_repo nexiafk`
- `!load nexiafk`

## 포함된 Cog
- nexiafk

## 명령어 정리

### 사용자 명령어
- `!afk` : AFK 토글
- `!afk status` : AFK 상태 확인
- `!afk set <message>` : 개인 AFK 멘트 설정 (1~200자, 최대 3줄)
- `!afk clearmsg` : 개인 AFK 멘트 삭제
- `!afk autoclear [on|off]` : 메시지 전송 시 AFK 자동 해제 토글 (기본 ON)
- `!afk auto [시간]` : 활동이 없을 때 자동 AFK 설정/토글 (예: 10m, 1h, 1d)

### 오너 전용 명령어
- `!afkadmin add <user>` : 허용 사용자 추가
- `!afkadmin remove <user>` : 허용 사용자 제거
- `!afkadmin list` : 허용 사용자 목록
- `!afkadmin reset` : 허용 사용자 목록 초기화
- `!afkadmin toggledefault` : 기본 멘트 변경 허용 토글
- `!afkadmin togglebots` : 봇 메시지 무시 토글
- `!afkadmin setdefault <message>` : 기본 AFK 멘트 변경 (토글 ON 필요)
- `!afkadmin toggleoffduty` : 닉네임 [OFFDUTY] 자동 AFK 토글
- `!afkadmin toggleoffduty` : 닉네임 [OFFDUTY] 자동 AFK 토글
