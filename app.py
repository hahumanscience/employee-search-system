import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai
import os
import json

# --- 1. 設定と初期化 ---

# ページ設定
st.set_page_config(page_title="AI社員検索システム", layout="wide")

# 修正後の app.py 初期化部分 (一部抜粋)
# --- 1. 設定と初期化 ---
# ... (st.set_page_configはそのまま)

# 環境変数の読み込み (Github CodespacesのSecretsから)
try:
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    firebase_key_json = os.environ.get("FIREBASE_KEY_JSON")
    
    # 必須のキーが存在するかチェック
    if not gemini_api_key:
        st.error("環境変数 'GEMINI_API_KEY' が設定されていません。Github Secretsを確認してください。")
        st.stop()
        
    if not firebase_key_json:
        st.error("環境変数 'FIREBASE_KEY_JSON' が設定されていません。Github Secretsを確認してください。")
        st.stop()
        
    # JSON文字列を辞書に変換
    firebase_key_dict = json.loads(firebase_key_json)

except json.JSONDecodeError:
    st.error("FIREBASE_KEY_JSONの値が不正な形式です。JSONのフォーマット（カンマ、クォーテーションなど）を確認してください。")
    st.stop()
except Exception as e:
    st.error(f"初期化中に予期せぬエラーが発生しました: {e}")
    st.stop()
    
# Gemini APIの設定
genai.configure(api_key=gemini_api_key)
model = genai.GenerativeModel('gemini-2.5-flash') # 軽量・高速なモデルを使用

# Firebaseの初期化 (二重初期化防止)
if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_key_dict)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# --- 2. 共通関数 (AI & DBロジック) ---

def extract_tags_with_gemini(text):
    """
    Geminiを使って自然言語からキーワードタグを抽出する関数
    """
    prompt = f"""
    以下のテキストから、職務経歴、スキル、特徴を表す重要なキーワードを5つから10個抽出してください。
    出力は、カンマ区切りの単語リストのみにしてください（例: Python, プロジェクトマネジメント, 営業, ...）。
    余計な文章は含めないでください。

    テキスト:
    {text}
    """
    try:
        response = model.generate_content(prompt)
        # カンマ区切りテキストをリストに変換し、空白除去
        tags = [t.strip() for t in response.text.split(',')]
        return tags
    except Exception as e:
        st.error(f"Gemini API Error: {e}")
        return []

def structure_description_with_gemini(text):
    """
    Geminiを使って自然言語の紹介文を構造化する関数 (Markdown形式)
    """
    prompt = f"""
    以下の社員紹介文を読んで、内容を最も適切に分類し、Markdown形式のリストで構造化してください。
    必ず以下の見出しを使用してください：- 職務経歴:, - スキル:, - その他特徴:
    分類が難しい情報や、上記に当てはまらない情報は「その他特徴」にまとめてください。
    分類した内容はその見出しの下に箇条書き（ハイフンとスペース）で記述してください。

    紹介文:
    {text}
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        st.error(f"Gemini API Error (Structure): {e}")
        # 失敗した場合でも元の文章はDBに保存されるため、ここではエラーメッセージを返す
        return "--- 構造化に失敗 ---"

def save_employee_data(name, description, tags, structured_description):
    """
    社員情報をFirestoreに保存/更新する関数 (社員名をIDとして使用)
    """
    # 変更点: 社員名をドキュメントIDとして使用
    doc_ref = db.collection('employees').document(name)
    
    data = {
        'name': name,
        'description': description, # 元の自然言語
        'structured_description': structured_description, # 構造化された文章 (新規追加)
        'tags': tags,
        'updated_at': firestore.SERVER_TIMESTAMP # 更新日時を記録
    }
    
    # set()でdocument IDを指定した場合、存在すれば更新、存在しなければ新規作成 (Upsert) される
    doc_ref.set(data)

def search_employees_by_tags(search_tags):
    """
    タグの一致度に基づいて社員を検索する関数
    """
    employees_ref = db.collection('employees')
    docs = employees_ref.stream()
    
    results = []
    search_set = set(search_tags) # 検索タグをセット化（高速化のため）

    for doc in docs:
        data = doc.to_dict()
        emp_tags = set(data.get('tags', []))
        
        # 積集合（共通するタグ）を取得
        match_tags = search_set.intersection(emp_tags)
        match_count = len(match_tags)

        if match_count > 0:
            results.append({
                'name': data['name'],
                'description': data['description'],
                'tags': data['tags'],
                'match_count': match_count,
                'matched_keywords': list(match_tags)
            })
    
    # 一致数が多い順にソート
    results.sort(key=lambda x: x['match_count'], reverse=True)
    return results

# --- 3. UI構築 (Streamlit) ---

st.title("🏢 AI社員スキルマッチングシステム")

# サイドバーでモード切替
menu = st.sidebar.selectbox("メニュー", ["社員登録 (Input)", "スキル検索 (Search)"])

if menu == "社員登録 (Input)":
    st.header("📝 社員情報の登録")
    st.write("自己紹介や職務経歴を自然言語で入力してください。AIが自動でタグ付けします。")

    with st.form("register_form"):
        name_input = st.text_input("社員名(ユニークIDとして使用されます)")
        desc_input = st.text_area("紹介文・スキル・経歴 (自然言語でOK)", height=150,
                                  placeholder="例: 新卒で入社後、Javaを用いたWeb開発に従事。現在はAWS構築を担当しており、Pythonでのデータ分析にも興味があります。")
        
        submitted = st.form_submit_button("登録＆AI解析")

        if submitted and name_input and desc_input:
            with st.spinner("Geminiがテキストを解析中..."):
                # 1. タグ抽出
                extracted_tags = extract_tags_with_gemini(desc_input)

                # 2. 構造化 (新規追加機能)
                structured_desc = structure_description_with_gemini(desc_input)               
                
                st.subheader("🤖 AIが抽出したタグ")

                # 3. DB保存/更新
                save_employee_data(name_input, desc_input, extracted_tags, structured_desc)
                st.success(f"**{name_input}** さんの情報をデータベースに登録/更新しました！")
                
                st.markdown("---")
                st.subheader("タグ付け結果")
                st.write(", ".join(extracted_tags))
                
                st.subheader("構造化されたプロフィール (格納形式)")
                # 構造化された結果をそのまま表示 (Markdownで整形される)
                st.markdown(structured_desc)
                
                # 2. DB保存
                save_employee_data(name_input, desc_input, extracted_tags, structured_desc)
                st.success(f"{name_input} さんの情報をデータベースに登録しました！")

elif menu == "スキル検索 (Search)":
    st.header("🔍 人材検索")
    st.write("探している人材の要件を自然言語で入力してください。")

    search_query = st.text_area("検索クエリ", height=100,
                                placeholder="例: クラウドインフラの構築ができて、かつデータ分析の知見があるエンジニアを探しています。")
    
    search_btn = st.button("AI検索開始")

    if search_btn and search_query:
        with st.spinner("Geminiが検索意図を解析し、DBと照合中..."):
            # 1. 検索文からタグ抽出
            query_tags = extract_tags_with_gemini(search_query)
            st.info(f"検索キーワード: {', '.join(query_tags)}")

            # 2. DB突合
            results = search_employees_by_tags(query_tags)

            # 3. 結果表示
            if results:
                st.subheader(f"マッチした社員: {len(results)}名")
                for i, emp in enumerate(results):
                    # Expanderを使ってクリックで開閉
                    with st.expander(f"#{i+1} {emp['name']} (マッチ度: {emp['match_count']})"):
                        st.markdown(f"**マッチしたキーワード:** `{', '.join(emp['matched_keywords'])}`")
                        st.markdown(f"**全タグ:** {', '.join(emp['tags'])}")
                        st.markdown("---")
                        st.write("**詳細プロフィール:**")
                        st.write(emp['description'])
            else:
                st.warning("条件に一致する社員は見つかりませんでした。")