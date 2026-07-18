"""CycleGen SaaS層パッケージ

CYCLEGEN_MODE=saas のときだけ有効化される認証・マルチテナント・Quota層。
既存のcore/search/persistenceには手を入れず、ミドルウェアとして分離する。

CYCLE8.1: パッケージ基盤（context, models, db, key_manager）
CYCLE8.2: 認証ミドルウェア + owner_idフィルタ
CYCLE8.3: Quota + レート制限
"""
