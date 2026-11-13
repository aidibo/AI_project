-- t_user 用户表建表脚本
CREATE TABLE IF NOT EXISTS t_user (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    username   VARCHAR(50)     NOT NULL                 COMMENT '用户名(登录账号)',
    password   VARCHAR(255)    NOT NULL                 COMMENT '密码(生产环境请存哈希值)',
    nickname   VARCHAR(50)              DEFAULT NULL    COMMENT '昵称',
    email      VARCHAR(100)             DEFAULT NULL    COMMENT '邮箱',
    phone      VARCHAR(20)              DEFAULT NULL    COMMENT '手机号',
    age        TINYINT UNSIGNED         DEFAULT NULL    COMMENT '年龄',
    gender     TINYINT         NOT NULL DEFAULT 0        COMMENT '性别 0未知 1男 2女',
    status     TINYINT         NOT NULL DEFAULT 1        COMMENT '状态 1正常 0禁用',
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                        ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username),
    UNIQUE KEY uk_email (email),
    KEY idx_phone (phone),
    KEY idx_status (status)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci COMMENT = '用户表';
