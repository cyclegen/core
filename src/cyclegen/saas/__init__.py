"""CycleGen SaaS層パッケージ

★Core配布物では Enterprise拡張点として常在する no-op シムのみ（context/guard）。
実体（認証・マルチテナント・Quota等）は Enterprise で加算される。Coreでは常に無効。

CYCLEGEN_MODE=saas のときだけ有効化される認証・マルチテナント・Quota層。
既存のcore/search/persistenceには手を入れず、ミドルウェアとして分離する。

CYCLE8.1: パッケージ基盤（context, models, db, key_manager）
CYCLE8.2: 認証ミドルウェア + owner_idフィルタ
CYCLE8.3: Quota + レート制限
"""
