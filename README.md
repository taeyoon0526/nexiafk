# AFKMention

Red-DiscordBot v3용 커스텀 AFK Cog입니다. 특정 허용 사용자만 AFK를 사용하며, 직접 멘션 시 자동 응답합니다.

## 설치 및 로드
- `[p]load afkmention`
- `[p]help afk`
- `[p]help afkadmin`

## 기본 명령어
- `[p]afk` : AFK 토글
- `[p]afk status` : 상태 확인
- `[p]afk set <message>` : 개인 멘트 설정
- `[p]afk clearmsg` : 개인 멘트 삭제
- `[p]afkadmin add <user>` : 허용 사용자 추가
- `[p]afkadmin remove <user>` : 허용 사용자 제거
- `[p]afkadmin list` : 허용 사용자 목록
- `[p]afkadmin reset` : 허용 사용자 목록 초기화
- `[p]afkadmin toggledefault` : 기본 멘트 변경 허용 토글
- `[p]afkadmin togglebots` : 봇 메시지 무시 토글
- `[p]afkadmin setdefault <message>` : 기본 AFK 멘트 변경 (토글 ON 필요)

## 테스트 시나리오 체크리스트
- [ ] 1) 기본 허용 검증
- [ ] 2) 자동 응답 트리거 검증
- [ ] 3) 트리거 금지 검증
- [ ] 4) 쿨다운 검증
- [ ] 5) 오너 관리 검증

