-- seed.sql - demo data for Learner Support Chat. Run AFTER schema.sql.
--
-- All dates are relative to "now" (SQLite datetime()), so the scenarios stay true
-- whenever you reset: "failed yesterday", "expired 3 days ago", etc.
-- Every user's password is: password123  (stored as a bcrypt hash)

-- ---------------------------------------------------------------- Users (ids 1-6)
INSERT INTO users (name, email, password_hash, role) VALUES
  ('Rahul Sharma',   'rahul@learner.com',  '$2b$12$rOrD5MVkAXSzPUVv0.4WX.35Lb663/cMAn4UiQVDEuzRMBN6GhalO', 'learner'),  -- 1
  ('Priya Verma',    'priya@learner.com',  '$2b$12$rOrD5MVkAXSzPUVv0.4WX.35Lb663/cMAn4UiQVDEuzRMBN6GhalO', 'learner'),  -- 2
  ('Aman Gupta',     'aman@learner.com',   '$2b$12$rOrD5MVkAXSzPUVv0.4WX.35Lb663/cMAn4UiQVDEuzRMBN6GhalO', 'learner'),  -- 3
  ('Neha Singh',     'neha@telusko.com',   '$2b$12$rOrD5MVkAXSzPUVv0.4WX.35Lb663/cMAn4UiQVDEuzRMBN6GhalO', 'support'),  -- 4
  ('Vikram Rao',     'vikram@telusko.com', '$2b$12$rOrD5MVkAXSzPUVv0.4WX.35Lb663/cMAn4UiQVDEuzRMBN6GhalO', 'support'),  -- 5
  ('Akshay Agarwal', 'akshay@telusko.com', '$2b$12$rOrD5MVkAXSzPUVv0.4WX.35Lb663/cMAn4UiQVDEuzRMBN6GhalO', 'admin');   -- 6

-- ---------------------------------------------------------------- Courses (ids 1-5), 1-year access
INSERT INTO courses (title, description, price_inr, access_days) VALUES
  ('Java Complete Course',            'Core Java, OOP, collections, streams, multithreading and JDBC.',  2999, 365),
  ('Spring Boot & Microservices',     'REST APIs, Spring Data JPA, Security, Eureka, API Gateway.',      3999, 365),
  ('DevOps with Docker & Kubernetes', 'Containers, Kubernetes, CI/CD pipelines and cloud deployment.',   4499, 365),
  ('Agentic AI with MCP',             'Build AI agents with tool calling and the Model Context Protocol.', 4999, 365),
  ('Python for Beginners',            'Python fundamentals, data structures and small projects.',        1499, 365);

-- ---------------------------------------------------------------- Enrollments
INSERT INTO enrollments (user_id, course_id, enrolled_at, access_valid_till, progress_percent, certificate_issued) VALUES
  (1, 1, datetime('now', 'localtime', '-200 days'), datetime('now', 'localtime', '+165 days'), 100, 0),  -- 1: Rahul Java 100%, NO certificate
  (1, 5, datetime('now', 'localtime', '-60 days'),  datetime('now', 'localtime', '+305 days'), 40,  0),  -- 2: Rahul Python 40%
  (2, 2, datetime('now', 'localtime', '-368 days'), datetime('now', 'localtime', '-3 days'),   65,  0),  -- 3: Priya Spring Boot EXPIRED 3 days ago
  (2, 1, datetime('now', 'localtime', '-30 days'),  datetime('now', 'localtime', '+335 days'), 25,  0),  -- 4: Priya Java 25%
  (3, 3, datetime('now', 'localtime', '-4 days'),   datetime('now', 'localtime', '+361 days'), 5,   0);  -- 5: Aman DevOps 4 days ago, 5%

-- ---------------------------------------------------------------- Payments
INSERT INTO payments (user_id, course_id, amount_inr, status, paid_at, reference) VALUES
  (1, 1, 2999, 'success', datetime('now', 'localtime', '-200 days'), 'pay_JAVA_R001'),    -- 1
  (1, 5, 1499, 'success', datetime('now', 'localtime', '-60 days'),  'pay_PY_R002'),      -- 2
  (1, 4, 4999, 'failed',  datetime('now', 'localtime', '-1 days'),   'pay_AI_R003'),      -- 3: Rahul Agentic AI FAILED yesterday
  (2, 2, 3999, 'success', datetime('now', 'localtime', '-368 days'), 'pay_SB_P001'),      -- 4
  (2, 1, 2999, 'success', datetime('now', 'localtime', '-30 days'),  'pay_JAVA_P002'),    -- 5
  (3, 3, 4499, 'success', datetime('now', 'localtime', '-4 days'),   'pay_DEVOPS_A001');  -- 6: Aman DevOps

-- ---------------------------------------------------------------- Tickets
INSERT INTO tickets (user_id, subject, category, priority, status, assigned_to, created_at, updated_at) VALUES
  (1, 'Completed Java course but no certificate',        'certificate', 'medium', 'open',        NULL,
      datetime('now', 'localtime', '-2 days'),  datetime('now', 'localtime', '-2 days')),   -- 1
  (1, 'Payment failed for Agentic AI but money debited', 'payment',     'high',   'in_progress', 4,
      datetime('now', 'localtime', '-1 days'),  datetime('now', 'localtime', '-1 days')),   -- 2 (Neha)
  (2, 'Spring Boot course access expired',               'access',      'high',   'open',        NULL,
      datetime('now', 'localtime', '-1 days'),  datetime('now', 'localtime', '-1 days')),   -- 3
  (2, 'Videos buffering on mobile',                      'technical',   'low',    'resolved',    5,
      datetime('now', 'localtime', '-20 days'), datetime('now', 'localtime', '-18 days')),  -- 4 (Vikram)
  (3, 'Kubernetes lab not starting in DevOps course',     'technical',   'medium', 'open',        NULL,
      datetime('now', 'localtime'),             datetime('now', 'localtime'));              -- 5

-- ---------------------------------------------------------------- Ticket messages (is_internal = 1 -> support/admin only)
INSERT INTO ticket_messages (ticket_id, sender_id, message, is_internal, created_at) VALUES
  (1, 1, 'Hi, I finished 100% of the Java course last week but my certificate is not showing.', 0,
      datetime('now', 'localtime', '-2 days')),
  (2, 1, 'My payment of Rs 4999 for Agentic AI failed but the amount was debited from my bank.', 0,
      datetime('now', 'localtime', '-1 days')),
  (2, 4, 'Hi Rahul, we are checking this with the payment gateway. Please allow 24 hours.', 0,
      datetime('now', 'localtime', '-1 days')),
  (2, 4, 'INTERNAL: Gateway shows txn pay_AI_R003 as failed - bank should auto-reverse in 5-7 days. Do NOT promise a manual reversal.', 1,
      datetime('now', 'localtime', '-1 days')),
  (3, 2, 'My Spring Boot access expired 3 days ago, I still have 35% left. Can you extend it?', 0,
      datetime('now', 'localtime', '-1 days')),
  (4, 2, 'Videos keep buffering on my phone.', 0,
      datetime('now', 'localtime', '-20 days')),
  (4, 5, 'Please lower the video quality to 480p from the settings icon. Let us know if it persists.', 0,
      datetime('now', 'localtime', '-19 days')),
  (4, 5, 'INTERNAL: CDN issue in Mumbai region on that day, fixed by infra team.', 1,
      datetime('now', 'localtime', '-18 days')),
  (5, 3, 'The Kubernetes lab in the DevOps course keeps showing "Starting..." and never loads.', 0,
      datetime('now', 'localtime'));

-- ---------------------------------------------------------------- FAQs
INSERT INTO faqs (question, answer, category) VALUES
  ('How long do I have access to a course?',
   'Every course comes with 1 year (365 days) of access from the date of purchase. If your access expired, raise a ticket and our support team can review an extension.',
   'access'),
  ('My payment failed but money was debited. What should I do?',
   'Failed payments are automatically reversed by your bank within 5-7 working days. If it is not reversed after 7 days, raise a payment ticket with your transaction reference.',
   'payment'),
  ('How do I get my course certificate?',
   'Certificates are issued once you complete 100% of the course. It usually appears within 48 hours. If not, raise a certificate ticket.',
   'certificate'),
  ('Videos are buffering. How do I fix it?',
   'Try lowering the video quality from the settings (gear) icon, switch to a stable network, or clear your browser cache. Mobile users can try the app.',
   'technical'),
  ('Can I download videos for offline viewing?',
   'Videos can be downloaded for offline viewing only inside the Telusko mobile app. Downloading via browser is not supported.',
   'technical'),
  ('Can I access courses on multiple devices?',
   'Yes, you can log in on up to 2 devices at the same time with the same account.',
   'access');