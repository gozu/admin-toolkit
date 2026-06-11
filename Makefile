# ============================================================================
# Makefile â Multi-target deploy:
#   - PROD: locked down via sudo wrappers + root-owned key
#   - DEV:  via DSS API using .dss-url + .dss-api-key files
#
# Claude workflow:
#   make deploy COMMIT_MSG="Deploy update"
#
# PROD wrappers installed by your setup script:
#   /data/dss-secure-actions/bin/dss_plugin_update_admin-toolkit
#   /data/dss-secure-actions/bin/dss_webapp_restart_DIAG_PARSER_BRANCH1
# ============================================================================

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -euo pipefail -c

.DEFAULT_GOAL := plugin

# ----------------------------
# Plugin metadata (from plugin.json)
# ----------------------------
plugin_id := $(shell cat plugin.json | python3 -c "import sys,json; print(str(json.load(sys.stdin)['id']).replace('/',''))")
plugin_version := $(shell cat plugin.json | python3 -c "import sys,json; print(str(json.load(sys.stdin)['version']).replace('/',''))")
archive_file_name := dss-plugin-$(plugin_id)-$(plugin_version).zip

remote_url := $(shell git config --get remote.origin.url || true)
last_commit_id := $(shell git rev-parse HEAD 2>/dev/null || true)

# ----------------------------
# Deploy behavior toggles
# ----------------------------
RESTART_AFTER_DEPLOY ?= 1
COMMIT_MSG ?= Deploy update

# Dev server (reads .dss-url / .dss-api-key)
DSS_URL ?= $(shell cat .dss-url 2>/dev/null || cat ~/.dss-url 2>/dev/null)
DSS_API_KEY ?= $(shell cat .dss-api-key 2>/dev/null || cat ~/.dss-api-key 2>/dev/null)
WEBAPP_PROJECT_KEY ?= $(shell cat .dss-project-key 2>/dev/null || echo PYTHONAUDIT_TEST)
WEBAPP_ID ?= $(shell cat .dss-webapp-id 2>/dev/null || echo nlJn2Gm)

# PROD deploy (locked down) wrappers
SECURE_DIST_DIR ?= $(CURDIR)/dist
SECURE_PLUGIN_WRAPPER ?= /Users/akaos/Documents/dss-secure-actions/bin/dss_plugin_update_admin-toolkit
SECURE_RESTART_WRAPPER ?= /Users/akaos/Documents/dss-secure-actions/bin/dss_webapp_restart_DIAG_PARSER_BRANCH1
SECURE_WEBAPP_ID ?= Gv9CLFn

# ----------------------------
# Safe commit paths for deploy
# ----------------------------
DEPLOY_COMMIT_PATHS := \
	plugin.json \
	code-env \
	parameter-sets \
	python-lib \
	python-runnables \
	webapps \
	resource/dist \
	resource/dbhealth-connection-choices.py \
	resource/frontend/src \
	resource/frontend/public \
	resource/frontend/index.html \
	resource/frontend/package.json \
	resource/frontend/package-lock.json \
	resource/frontend/vite.config.ts \
	resource/frontend/tsconfig.json \
	resource/frontend/tsconfig.app.json \
	resource/frontend/tsconfig.node.json \
	resource/frontend/eslint.config.js \
	scripts \
	Makefile \
	.gitattributes

# ----------------------------
# Frontend incremental build
# ----------------------------
FRONTEND_STAMP := resource/dist/.build-stamp
NODE_MODULES_STAMP := resource/frontend/node_modules/.install-stamp
FRONTEND_DEPS := $(shell find resource/frontend/src resource/frontend/public -type f 2>/dev/null) \
	resource/frontend/index.html \
	resource/frontend/package.json \
	resource/frontend/package-lock.json \
	resource/frontend/vite.config.ts \
	resource/frontend/tsconfig.json \
	resource/frontend/tsconfig.app.json \
	resource/frontend/tsconfig.node.json

# Only reinstall deps when the lockfile actually changes (a version-only bump to
# package.json does not alter the dependency tree, so it must not trigger npm install).
$(NODE_MODULES_STAMP): resource/frontend/package-lock.json
	@echo "[INFO] Lockfile changed — installing frontend dependencies..."
	cd resource/frontend && npm install
	@touch $@

$(FRONTEND_STAMP): $(FRONTEND_DEPS) $(NODE_MODULES_STAMP)
	@echo "[START] Building frontend..."
	cd resource/frontend && MODE=production npm run build
	mkdir -p resource/dist
	touch $@
	@echo "[SUCCESS] Frontend build complete!"

.PHONY: build-frontend
build-frontend: $(FRONTEND_STAMP)

# ----------------------------
# Claude field kit (context tarball for customer machines)
# ----------------------------
.PHONY: field-kit
field-kit:
	@bash scripts/field_kit.sh

# ----------------------------
# Build plugin ZIP (prod/dev)
# ----------------------------
.PHONY: plugin dev dist-clean clean

plugin: dist-clean $(FRONTEND_STAMP)
	@echo "[START] Archiving plugin to dist/ folder..."
	python3 -c "import json; json.load(open('plugin.json'))" || (echo "[ERROR] Invalid plugin.json"; exit 1)
	@# Safety: abort if resource/dist has uncommitted changes
	@if git status --porcelain resource/dist | grep -q .; then \
		echo "[ERROR] resource/dist has uncommitted changes — commit them first (run make deploy)."; \
		exit 1; \
	fi
	mkdir -p dist
	echo "{\"remote_url\":\"${remote_url}\",\"last_commit_id\":\"${last_commit_id}\"}" > release_info.json
	git archive -v -9 --format zip -o "dist/$(archive_file_name)" HEAD
	zip -u "dist/$(archive_file_name)" release_info.json
	rm -f release_info.json
	@echo "[SUCCESS] Archiving plugin: dist/$(archive_file_name)"

dev: dist-clean build-frontend
	@echo "[START] Archiving plugin to dist/ folder... (dev mode)"
	python3 -c "import json; json.load(open('plugin.json'))" || (echo "[ERROR] Invalid plugin.json"; exit 1)
	mkdir -p dist
	zip -v -9 "dist/$(archive_file_name)" -r . \
		--exclude "resource/frontend/node_modules/*" \
		--exclude "resource/frontend/.git/*" \
		--exclude "dist/*" \
		--exclude ".git/*" \
		--exclude "*.DS_Store" \
		--exclude "__pycache__/*" \
		--exclude "*.pyc" \
		--exclude "plugins.html" \
		--exclude "webapps.html" \
		--exclude ".dss-url" \
		--exclude ".dss-api-key" \
		--exclude ".dss-*" \
		--exclude ".airtable-token" \
		--exclude "bestofboth/*" \
		--exclude ".gitignore"
	@echo "[SUCCESS] Dev archive: dist/$(archive_file_name)"

dist-clean:
	rm -rf dist

clean: dist-clean
	rm -rf resource/dist
	rm -rf resource/frontend/node_modules

# ----------------------------
# Secure PROD deploy
# ----------------------------
.PHONY: secure-copy-zip deploy-prod-secure

secure-copy-zip:
	@mkdir -p "$(SECURE_DIST_DIR)"
	@if [ "$$(cd "$(SECURE_DIST_DIR)" && pwd)" != "$$(cd dist && pwd)" ]; then \
		cp -f "dist/$(archive_file_name)" "$(SECURE_DIST_DIR)/$(archive_file_name)"; \
		echo "[SUCCESS] Copied plugin ZIP to $(SECURE_DIST_DIR)/$(archive_file_name)"; \
	else \
		echo "[INFO] SECURE_DIST_DIR is the local dist/ — using built ZIP in place."; \
	fi

deploy-prod-secure: secure-copy-zip
	@echo "[START] Deploying to PROD (secure wrappers)..."
	@sudo "$(SECURE_PLUGIN_WRAPPER)" "$(archive_file_name)" 2>&1 \
		| python3 -c "import sys,json;r=json.load(sys.stdin);v=r.get('pluginDesc',{}).get('desc',{}).get('version','?');print('[SUCCESS] PROD plugin v%s installed'%v if r.get('messages',{}).get('success') else '[FAIL] PROD install failed')"
	@if [ "$(RESTART_AFTER_DEPLOY)" = "1" ]; then \
		echo "[START] Restarting PROD webapp backend (secure wrapper)..."; \
		sudo "$(SECURE_RESTART_WRAPPER)" "$(SECURE_WEBAPP_ID)" > /dev/null 2>&1; \
		echo "[SUCCESS] PROD webapp backend restart requested."; \
	fi

# ----------------------------
# Dev server deploy (via DSS API)
# ----------------------------
.PHONY: deploy-dev

deploy-dev:
	@echo "[START] Deploying to DEV server..."
	@if [ -z "$(DSS_URL)" ] || [ -z "$(DSS_API_KEY)" ]; then \
		echo "[WARN] Missing .dss-url or .dss-api-key — skipping dev deploy."; \
	else \
		echo "[INFO] Attempting update on $(DSS_URL)..."; \
		UPDATE_RESPONSE=$$(DSS_URL="$(DSS_URL)" DSS_API_KEY="$(DSS_API_KEY)" bash scripts/dss_api.sh POST "/public/api/plugins/$(plugin_id)/actions/updateFromZip" \
			--form "file=@dist/$(archive_file_name)"); \
		UPDATE_CODE=$$(echo "$$UPDATE_RESPONSE" | tail -n1); \
		if [ "$$UPDATE_CODE" = "204" ] || [ "$$UPDATE_CODE" = "200" ]; then \
			echo "[SUCCESS] Plugin updated on DEV!"; \
		else \
			echo "[INFO] Update failed (HTTP $$UPDATE_CODE), attempting install..."; \
			INSTALL_RESPONSE=$$(DSS_URL="$(DSS_URL)" DSS_API_KEY="$(DSS_API_KEY)" bash scripts/dss_api.sh POST "/public/api/plugins/actions/installFromZip" \
				--form "file=@dist/$(archive_file_name)"); \
			INSTALL_CODE=$$(echo "$$INSTALL_RESPONSE" | tail -n1); \
			if [ "$$INSTALL_CODE" = "204" ] || [ "$$INSTALL_CODE" = "200" ]; then \
				echo "[SUCCESS] Plugin installed on DEV!"; \
			else \
				echo "[ERROR] DEV deploy failed (update: $$UPDATE_CODE, install: $$INSTALL_CODE)"; \
			fi; \
		fi; \
		if [ "$(RESTART_AFTER_DEPLOY)" = "1" ] && [ -n "$(WEBAPP_PROJECT_KEY)" ] && [ -n "$(WEBAPP_ID)" ]; then \
			echo "[START] Restarting DEV webapp backend..."; \
			DSS_URL="$(DSS_URL)" DSS_API_KEY="$(DSS_API_KEY)" bash scripts/dss_api.sh PUT "/public/api/projects/$(WEBAPP_PROJECT_KEY)/webapps/$(WEBAPP_ID)/backend/actions/restart" >/dev/null || true; \
			echo "[SUCCESS] DEV webapp backend restart requested."; \
		fi; \
	fi

# ----------------------------
# Deploy all targets
# ----------------------------
.PHONY: deploy-all deploy

# DEV (akaos, via API) and PROD (secure sudo wrappers) are independent targets on
# different servers — run them concurrently and fail if either side fails.
deploy-all:
	@$(MAKE) deploy-dev & pid_dev=$$!; \
	$(MAKE) deploy-prod-secure & pid_prod=$$!; \
	rc=0; \
	wait $$pid_dev || rc=1; \
	wait $$pid_prod || rc=1; \
	exit $$rc

# ----------------------------
# Main deploy target
# ----------------------------
deploy:
	@echo "[START] Bumping version..."
	@python3 -c "import json,pathlib; pf=pathlib.Path('plugin.json'); d=json.loads(pf.read_text()); p=d['version'].split('.'); b=int(p[2])+1; nv=('%s.%d.%03d'%(p[0],int(p[1])+1,0)) if b>999 else ('%s.%s.%03d'%(p[0],p[1],b)); print('plugin.json: %s -> %s'%(d['version'],nv)); d['version']=nv; pf.write_text(json.dumps(d,indent=4)+chr(10)); pkg=pathlib.Path('resource/frontend/package.json'); exec('p2=json.loads(pkg.read_text());print(\"package.json: %s -> %s\"%(p2[\"version\"],nv));p2[\"version\"]=nv;pkg.write_text(json.dumps(p2,indent=2)+chr(10))') if pkg.exists() else None"
	@$(MAKE) build-frontend
	@echo "[START] Committing deploy-relevant changes..."
	@git add -- $(DEPLOY_COMMIT_PATHS)
	@git commit -m "$(COMMIT_MSG)" -- $(DEPLOY_COMMIT_PATHS) || echo "[INFO] Nothing deploy-relevant to commit"
	@$(MAKE) plugin
	@$(MAKE) deploy-all

# ----------------------------
# Dual-LLM pre-push security gate (all logic lives in scripts/secure-push.sh)
# ----------------------------
.PHONY: secure-push install-hooks

secure-push:        ## Dual-LLM security review, then push only if both approve
	@./scripts/secure-push.sh

install-hooks:      ## Route git's pre-push through the security gate
	@git config core.hooksPath scripts/hooks && echo "pre-push gate installed (core.hooksPath=scripts/hooks)"
