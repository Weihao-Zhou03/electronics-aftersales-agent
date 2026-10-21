# Explicit local demo bootstrap only. Capture stdout privately; never log the JSON tokens.
require 'json'
raise 'demo only' unless ENV.fetch('APP_MODE', '') == 'demo'

account = Account.find_or_create_by!(name: 'electronics-aftersales-agent') { |a| a.locale = 'zh_CN' }
def provision_user(email, name, password)
  raise 'password is required' if password.length < 20
  user = User.find_or_initialize_by(email: email)
  if user.new_record?
    user.name = name
    user.password = password
    user.password_confirmation = password
    user.skip_confirmation!
    user.save!
  end
  user
end
admin = provision_user('demo-admin@example.invalid', 'Demo Administrator', ENV.fetch('CLEAN_ADMIN_PASSWORD'))
reader = provision_user('demo-reader@example.invalid', 'Dedicated Runtime Reader', ENV.fetch('CLEAN_READER_PASSWORD'))
AccountUser.find_or_create_by!(account: account, user: admin) { |membership| membership.role = 'administrator' }
AccountUser.find_or_create_by!(account: account, user: reader) { |membership| membership.role = 'agent' }
puts({ account_id: account.id, admin_token: admin.access_token.token,
       reader_token: reader.access_token.token, reader_id: reader.id }.to_json)
